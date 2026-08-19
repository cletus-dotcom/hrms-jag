"""Overnight flexi punch pairing: move next-morning Out onto the shift-start DTR day."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

from app import db
from app.models import DailyTimeRecord, Employee, FlexibleWorktime


def _hm_minutes(hm: tuple[int, int]) -> int:
    return int(hm[0]) * 60 + int(hm[1])


def _time_minutes(t: time | None) -> int | None:
    if t is None:
        return None
    return t.hour * 60 + t.minute


def duty_schedule_is_overnight(sched: dict | None) -> bool:
    """True when Out is earlier on the clock than In (crosses midnight)."""
    if not sched:
        return False
    if ((sched.get('duty_mode') or '') or '').strip().lower() == 'rest':
        return False
    try:
        return _hm_minutes(sched['pm_out']) < _hm_minutes(sched['am_in'])
    except (KeyError, TypeError, IndexError):
        return False


def _day_out_time(rec: DailyTimeRecord | None) -> time | None:
    if not rec:
        return None
    return rec.pm_out or rec.am_out


def _day_has_usable_out(rec: DailyTimeRecord | None) -> bool:
    return _day_out_time(rec) is not None


def is_next_morning_out_for_overnight(out_t: time, sched: dict, *, grace_mins: int = 120) -> bool:
    """
    Candidate Out on calendar day D+1 belongs to overnight shift that started on D
    when it falls in the early-morning window before that shift's time-in.
    """
    if not out_t or not sched:
        return False
    tin = _hm_minutes(sched['am_in'])
    tout = _hm_minutes(sched['pm_out'])
    out_m = _time_minutes(out_t)
    if out_m is None or out_m >= tin:
        return False
    # Allow through scheduled out + grace, and at least through 06:00 for midnight shifts.
    upper = min(tin - 1, max(tout + max(0, grace_mins), 6 * 60))
    return out_m <= upper


@dataclass(frozen=True)
class OvernightPunchMove:
    employee_id: int
    badge_id: str
    employee_name: str
    start_date: date
    source_date: date
    out_time: time
    source_slot: str  # pm_out | am_out
    schedule_in: str
    schedule_out: str


def _fmt_hm(hm: tuple[int, int]) -> str:
    return f'{int(hm[0]):02d}:{int(hm[1]):02d}'


def plan_overnight_out_moves_for_employee(
    *,
    employee: Employee,
    flexi_by_date: dict[date, dict],
    dtr_by_date: dict[date, DailyTimeRecord],
    range_start: date,
    range_end: date,
) -> list[OvernightPunchMove]:
    """
    Propose moving early-morning Out from day D+1 onto overnight flexi start day D
    when D is missing Out. Does not mutate rows.
    """
    moves: list[OvernightPunchMove] = []
    # Work on a shadow of outs already claimed / present so chained days work in one pass.
    shadow_out: dict[date, time | None] = {}
    shadow_cleared_pm: set[date] = set()
    shadow_cleared_am: set[date] = set()

    def effective_out(d: date) -> time | None:
        if d in shadow_out:
            return shadow_out[d]
        rec = dtr_by_date.get(d)
        if not rec:
            return None
        if d in shadow_cleared_pm and d in shadow_cleared_am:
            return None
        if rec.pm_out and d not in shadow_cleared_pm:
            return rec.pm_out
        if rec.am_out and d not in shadow_cleared_am:
            return rec.am_out
        return None

    def source_out_slot(d: date) -> tuple[time, str] | None:
        rec = dtr_by_date.get(d)
        if not rec:
            return None
        if rec.pm_out and d not in shadow_cleared_pm:
            return rec.pm_out, 'pm_out'
        if rec.am_out and d not in shadow_cleared_am:
            return rec.am_out, 'am_out'
        return None

    curr = range_start
    while curr <= range_end:
        sched = flexi_by_date.get(curr)
        if duty_schedule_is_overnight(sched) and effective_out(curr) is None:
            nxt = curr + timedelta(days=1)
            src = source_out_slot(nxt)
            if src and is_next_morning_out_for_overnight(src[0], sched):
                out_t, slot = src
                moves.append(OvernightPunchMove(
                    employee_id=employee.id,
                    badge_id=(employee.employee_id or '').strip(),
                    employee_name=employee.directory_name or employee.full_name or f'#{employee.id}',
                    start_date=curr,
                    source_date=nxt,
                    out_time=out_t,
                    source_slot=slot,
                    schedule_in=_fmt_hm(sched['am_in']),
                    schedule_out=_fmt_hm(sched['pm_out']),
                ))
                shadow_out[curr] = out_t
                if slot == 'pm_out':
                    shadow_cleared_pm.add(nxt)
                else:
                    shadow_cleared_am.add(nxt)
                # If both cleared, next effective_out(nxt) is None unless only one was set.
        curr += timedelta(days=1)
    return moves


def apply_overnight_out_moves(
    moves: list[OvernightPunchMove],
    dtr_by_key: dict[tuple[int, date], DailyTimeRecord],
) -> int:
    """
    Apply planned moves onto DailyTimeRecord instances already loaded in ``dtr_by_key``.
    Returns number of applied moves. Caller commits.
    """
    applied = 0
    for mv in moves:
        start = dtr_by_key.get((mv.employee_id, mv.start_date))
        source = dtr_by_key.get((mv.employee_id, mv.source_date))
        if source is None:
            continue
        src_val = getattr(source, mv.source_slot, None)
        if src_val != mv.out_time:
            # Row changed since preview; skip safely.
            continue
        if start is None:
            start = DailyTimeRecord(employee_id=mv.employee_id, record_date=mv.start_date)
            db.session.add(start)
            dtr_by_key[(mv.employee_id, mv.start_date)] = start
        if start.pm_out is not None or start.am_out is not None:
            continue
        start.pm_out = mv.out_time
        setattr(source, mv.source_slot, None)
        applied += 1
    return applied


def flexi_schedule_map_for_employee(
    emp_id: int,
    year: int,
    month: int,
    quincena_half: str,
    start_d: date,
    end_d: date,
    *,
    duty_schedule_from_row,
    entry_date_bounds,
) -> dict[date, dict]:
    """Build date→duty schedule using caller-provided converters (avoids circular imports)."""
    rows = (
        FlexibleWorktime.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        )
        .order_by(FlexibleWorktime.date_start.asc(), FlexibleWorktime.id.asc())
        .all()
    )
    win_start, win_end = entry_date_bounds(start_d, end_d)
    sched_by_date: dict[date, dict] = {}
    for row in rows:
        sched = duty_schedule_from_row(row)
        curr = max(row.date_start, win_start)
        stop = min(row.date_end, win_end)
        while curr <= stop:
            sched_by_date[curr] = sched
            curr += timedelta(days=1)
    return sched_by_date


def pair_overnight_punches_for_quincena(
    *,
    year: int,
    month: int,
    quincena_half: str,
    start_d: date,
    end_d: date,
    employees: list[Employee],
    duty_schedule_from_row,
    entry_date_bounds,
    employee_on_flexi,
    dry_run: bool = False,
) -> tuple[list[OvernightPunchMove], int]:
    """
    Preview or apply overnight Out pairing for all given employees on the flexi list.
    Extends read window by +1 day so outs stamped on the morning after range_end are visible.
    """
    if not employees:
        return [], 0
    read_end = end_d + timedelta(days=1)
    flexi_emps = [e for e in employees if employee_on_flexi(e.id, year, month, quincena_half)]
    if not flexi_emps:
        return [], 0

    dtr_rows = (
        DailyTimeRecord.query
        .filter(DailyTimeRecord.employee_id.in_([e.id for e in flexi_emps]))
        .filter(DailyTimeRecord.record_date >= start_d)
        .filter(DailyTimeRecord.record_date <= read_end)
        .all()
    )
    dtr_by_emp_date: dict[int, dict[date, DailyTimeRecord]] = {}
    dtr_by_key: dict[tuple[int, date], DailyTimeRecord] = {}
    for r in dtr_rows:
        dtr_by_emp_date.setdefault(r.employee_id, {})[r.record_date] = r
        dtr_by_key[(r.employee_id, r.record_date)] = r

    all_moves: list[OvernightPunchMove] = []
    for emp in flexi_emps:
        flexi_by_date = flexi_schedule_map_for_employee(
            emp.id, year, month, quincena_half, start_d, end_d,
            duty_schedule_from_row=duty_schedule_from_row,
            entry_date_bounds=entry_date_bounds,
        )
        if not flexi_by_date:
            continue
        moves = plan_overnight_out_moves_for_employee(
            employee=emp,
            flexi_by_date=flexi_by_date,
            dtr_by_date=dtr_by_emp_date.get(emp.id, {}),
            range_start=start_d,
            range_end=end_d,
        )
        all_moves.extend(moves)

    if dry_run:
        return all_moves, 0
    applied = apply_overnight_out_moves(all_moves, dtr_by_key)
    return all_moves, applied
