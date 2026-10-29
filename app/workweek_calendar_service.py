"""Office workweek calendar: month default (4-day / 5-day) and Friday-holiday weeks."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from flask import g

from app import db
from app.leave_ledger_service import LEAVE_CREDITS_STATUSES, recompute_leave_ledger_balances
from app.leave_utils import LEAVE_DAY_CREDIT_4DAY, LEAVE_DAY_CREDIT_5DAY, leave_day_credit_for_workweek
from app.models import Employee, LeaveLedger, WorkweekMonthPlan, WorkweekWeek
from app.ph_holidays import (
    friday_holidays_touching_year,
    iso_week_friday,
    iso_week_monday,
    mondays_overlapping_month,
)

FIVE_DAY_REMARK = '5-day workweek'
_REMARK_STRIP = re.compile(r'(?:^|;\s*)5-day workweek(?=\s*;|$)', re.IGNORECASE)
_FULL_DAY = {LEAVE_DAY_CREDIT_5DAY, LEAVE_DAY_CREDIT_4DAY}


def assert_workweek_calendar_editor() -> None:
    """Workweek calendar may be viewed or saved only by Admin, HR, or Portal Admin."""
    from flask_login import current_user

    from app.portal_config import is_admin_or_hr_role

    if not getattr(current_user, 'is_authenticated', False):
        raise PermissionError('Workweek calendar is for Admin and HR only.')
    if not is_admin_or_hr_role(getattr(current_user, 'role', None)):
        raise PermissionError('Workweek calendar is for Admin and HR only.')


def _clear_calendar_request_cache() -> None:
    try:
        g.pop('_workweek_month_plans', None)
        g.pop('_workweek_weeks', None)
    except RuntimeError:
        pass


def _month_plans_cached() -> dict[tuple[int, int], WorkweekMonthPlan]:
    try:
        cache = getattr(g, '_workweek_month_plans', None)
    except RuntimeError:
        cache = None
    if cache is None:
        cache = {(r.year, r.month): r for r in WorkweekMonthPlan.query.all()}
        try:
            g._workweek_month_plans = cache
        except RuntimeError:
            pass
    return cache


def _weeks_cached() -> dict[date, WorkweekWeek]:
    try:
        cache = getattr(g, '_workweek_weeks', None)
    except RuntimeError:
        cache = None
    if cache is None:
        cache = {r.week_start: r for r in WorkweekWeek.query.all()}
        try:
            g._workweek_weeks = cache
        except RuntimeError:
            pass
    return cache


def _model_from_maps(
    d: date,
    weeks: dict[date, WorkweekWeek],
    plans: dict[tuple[int, int], WorkweekMonthPlan],
) -> str | None:
    week = weeks.get(iso_week_monday(d))
    if week and week.flagged_5day:
        return '5day'
    plan = plans.get((d.year, d.month))
    if not plan:
        return None
    code = (plan.default_model or '').strip().lower()
    if code in ('4day', '5day'):
        return code
    return None


def calendar_model_code_for_date(d: date) -> str | None:
    """
    Saved calendar model for ``d``, or None if that month was never saved.

    Flagged Friday-holiday weeks are always ``5day``. Otherwise the month plan
    of ``d``'s calendar month is used (so a week may still be 4-day until flagged).
    """
    if d is None:
        return None
    try:
        return _model_from_maps(d, _weeks_cached(), _month_plans_cached())
    except Exception:
        return None


def apply_five_day_remark(remarks: str | None, flagged: bool) -> str | None:
    text = _REMARK_STRIP.sub('', remarks or '')
    text = re.sub(r'\s*;\s*;', '; ', text).strip(' ;')
    if flagged:
        text = f'{text}; {FIVE_DAY_REMARK}' if text else FIVE_DAY_REMARK
    return text or None


def rescale_full_day_charge(value, new_charge: Decimal) -> Decimal:
    v = Decimal('0') if value is None else Decimal(str(value))
    if v == 0:
        return v
    sign = Decimal('-1') if v < 0 else Decimal('1')
    mag = abs(v)
    if mag in _FULL_DAY:
        return sign * new_charge
    return v


def _kind_label(kind: str | None) -> str:
    k = (kind or '').strip().lower()
    if k == 'regular':
        return 'Regular holiday'
    if k == 'special_non_working':
        return 'Special non-working'
    if k == 'local':
        return 'Local holiday'
    return (kind or '').strip() or '—'


def build_year_view(year: int) -> dict[str, Any]:
    """Months + weeks for the workweek calendar page (saved rows overlay auto Friday holidays)."""
    assert_workweek_calendar_editor()
    friday_hols = {h.date: h for h in friday_holidays_touching_year(year)}
    week_starts: set[date] = set()
    for month in range(1, 13):
        week_starts.update(mondays_overlapping_month(year, month))

    saved_weeks = {
        row.week_start: row
        for row in WorkweekWeek.query.filter(WorkweekWeek.week_start.in_(list(week_starts))).all()
    } if week_starts else {}
    saved_plans = {
        row.month: row
        for row in WorkweekMonthPlan.query.filter_by(year=year).all()
    }

    months = []
    emitted_week_fields: set[date] = set()
    for month in range(1, 13):
        plan = saved_plans.get(month)
        default_model = (plan.default_model if plan else '4day')
        if default_model not in ('4day', '5day'):
            default_model = '4day'
        weeks = []
        for monday in mondays_overlapping_month(year, month):
            friday = iso_week_friday(monday)
            if friday.year == year:
                emit_fields = friday.month == month
            else:
                emit_fields = monday not in emitted_week_fields
            emitted_week_fields.add(monday)
            sunday = monday + timedelta(days=6)
            auto = friday_hols.get(friday)
            saved = saved_weeks.get(monday)
            if saved:
                flagged = bool(saved.flagged_5day)
                holiday_name = saved.holiday_name or (auto.name if auto else '')
                holiday_kind = saved.holiday_kind or (auto.kind if auto else '')
                holiday_source = saved.holiday_source or ('ph_official' if auto else '')
                holiday_date = saved.holiday_date or (auto.date if auto else None)
                local_name = saved.holiday_name if (saved.holiday_kind or '') == 'local' else ''
                if saved.holiday_kind == 'local':
                    holiday_name = saved.holiday_name or ''
            else:
                flagged = auto is not None
                holiday_name = auto.name if auto else ''
                holiday_kind = auto.kind if auto else ''
                holiday_source = 'ph_official' if auto else ''
                holiday_date = auto.date if auto else None
                local_name = ''
            if flagged or default_model == '5day':
                effective = '5day'
            else:
                effective = '4day'
            weeks.append({
                'week_start': monday,
                'week_start_iso': monday.isoformat(),
                'week_end': sunday,
                'friday': friday,
                'friday_iso': friday.isoformat(),
                'label': f'{monday.strftime("%b %d")} – {sunday.strftime("%b %d, %Y")}',
                'flagged': flagged,
                'holiday_name': holiday_name,
                'holiday_kind': holiday_kind,
                'holiday_kind_label': _kind_label(holiday_kind),
                'holiday_source': holiday_source,
                'holiday_date': holiday_date,
                'local_name': local_name,
                'auto_friday': auto is not None,
                'effective_model': effective,
                'spans_other_year': monday.year != year or sunday.year != year,
                'emit_fields': emit_fields,
            })
        months.append({
            'month': month,
            'name': calendar.month_name[month],
            'default_model': default_model,
            'saved': plan is not None,
            'weeks': weeks,
        })

    return {
        'year': year,
        'months': months,
        'friday_holiday_count': len(friday_hols),
        'friday_holidays': [
            {
                'date': h.date,
                'name': h.name,
                'kind': h.kind,
                'kind_label': _kind_label(h.kind),
                'week_start': iso_week_monday(h.date),
            }
            for h in sorted(friday_hols.values(), key=lambda x: x.date)
        ],
    }


def save_year_calendar(year: int, form, *, user_id: int | None) -> dict[str, int]:
    """Persist month defaults + week flags from the workweek calendar form. Recomputes leave."""
    assert_workweek_calendar_editor()
    old_weeks = dict(_weeks_cached())
    old_plans = dict(_month_plans_cached())
    old_by_date: dict[date, tuple[str | None, bool]] = {}
    span_start = iso_week_monday(date(year, 1, 1))
    span_end = iso_week_monday(date(year, 12, 31)) + timedelta(days=6)
    d = span_start
    while d <= span_end:
        week = old_weeks.get(iso_week_monday(d))
        flagged = bool(week.flagged_5day) if week else False
        old_by_date[d] = (_model_from_maps(d, old_weeks, old_plans), flagged)
        d += timedelta(days=1)

    for month in range(1, 13):
        raw = (form.get(f'month_{month}') or '4day').strip().lower()
        if raw not in ('4day', '5day'):
            raw = '4day'
        row = WorkweekMonthPlan.query.filter_by(year=year, month=month).first()
        if not row:
            row = WorkweekMonthPlan(year=year, month=month)
            db.session.add(row)
        row.default_model = raw
        row.updated_by_user_id = user_id
        row.updated_at = datetime.utcnow()

    week_starts = form.getlist('week_start')
    seen: set[date] = set()
    for raw_start in week_starts:
        try:
            monday = date.fromisoformat((raw_start or '').strip())
        except ValueError:
            continue
        if monday in seen:
            continue
        seen.add(monday)
        key = monday.isoformat()
        flagged = (form.get(f'flag_{key}') or '').strip() in ('1', 'on', 'true', 'yes')
        local_name = (form.get(f'local_{key}') or '').strip()
        auto_name = (form.get(f'hname_{key}') or '').strip()
        auto_kind = (form.get(f'hkind_{key}') or '').strip()
        auto_src = (form.get(f'hsrc_{key}') or '').strip()
        auto_date_raw = (form.get(f'hdate_{key}') or '').strip()
        auto_date = None
        if auto_date_raw:
            try:
                auto_date = date.fromisoformat(auto_date_raw)
            except ValueError:
                auto_date = None

        row = WorkweekWeek.query.filter_by(week_start=monday).first()
        if not row:
            row = WorkweekWeek(week_start=monday)
            db.session.add(row)
        row.flagged_5day = flagged
        if local_name:
            row.holiday_name = local_name[:180]
            row.holiday_kind = 'local'
            row.holiday_source = 'local'
            row.holiday_date = iso_week_friday(monday)
        elif auto_name:
            row.holiday_name = auto_name[:180]
            row.holiday_kind = auto_kind or None
            row.holiday_source = auto_src or 'ph_official'
            row.holiday_date = auto_date or iso_week_friday(monday)
        else:
            row.holiday_name = None
            row.holiday_kind = None
            row.holiday_source = None
            row.holiday_date = None
        if flagged and not row.holiday_date:
            row.holiday_date = iso_week_friday(monday)
        row.updated_by_user_id = user_id
        row.updated_at = datetime.utcnow()

    db.session.flush()
    _clear_calendar_request_cache()

    changed_dates: list[date] = []
    new_weeks = _weeks_cached()
    d = span_start
    while d <= span_end:
        new_model = calendar_model_code_for_date(d)
        week = new_weeks.get(iso_week_monday(d))
        new_flagged = bool(week.flagged_5day) if week else False
        old_model, old_flagged = old_by_date.get(d, (None, False))
        if new_model != old_model or new_flagged != old_flagged:
            changed_dates.append(d)
        d += timedelta(days=1)

    ledger_rows = 0
    employees = 0
    if changed_dates:
        ledger_rows, employees = recompute_leave_charges_for_dates(changed_dates)

    return {
        'year': year,
        'weeks_saved': len(seen),
        'dates_changed': len(changed_dates),
        'ledger_rows': ledger_rows,
        'employees': employees,
    }


def recompute_leave_charges_for_dates(dates: list[date]) -> tuple[int, int]:
    """Reset full-day VL/SL/LWOP charges on ledger rows for the given dates."""
    if not dates:
        return 0, 0
    uniq = sorted(set(dates))
    rows = (
        LeaveLedger.query
        .filter(LeaveLedger.transaction_date.in_(uniq))
        .all()
    )
    if not rows:
        return 0, 0
    emp_ids = {r.employee_id for r in rows}
    plantilla_ids = {
        e.id
        for e in Employee.query.filter(
            Employee.id.in_(list(emp_ids)),
            Employee.status_of_appointment.in_(LEAVE_CREDITS_STATUSES),
        ).all()
    }
    flexi_ids = {
        e.id
        for e in Employee.query.filter(
            Employee.id.in_(list(plantilla_ids)),
            Employee.flexible_worktime.is_(True),
        ).all()
    }
    touched_emps: set[int] = set()
    updated = 0
    for row in rows:
        if row.employee_id not in plantilla_ids or row.employee_id in flexi_ids:
            continue
        d = row.transaction_date
        if d.weekday() > 4:
            continue
        model = calendar_model_code_for_date(d) or '5day'
        charge = leave_day_credit_for_workweek(model)
        week = _weeks_cached().get(iso_week_monday(d))
        flagged = bool(week.flagged_5day) if week else False
        changed = False
        for field in ('vl_applied', 'sl_applied', 'lwop_days'):
            cur = getattr(row, field)
            nxt = rescale_full_day_charge(cur, charge)
            if nxt != Decimal(str(cur or 0)):
                setattr(row, field, nxt)
                changed = True
        new_remarks = apply_five_day_remark(row.remarks, flagged)
        if (new_remarks or '') != (row.remarks or ''):
            row.remarks = new_remarks
            changed = True
        if changed:
            updated += 1
            touched_emps.add(row.employee_id)

    db.session.flush()
    for eid in touched_emps:
        recompute_leave_ledger_balances(eid)
    return updated, len(touched_emps)
