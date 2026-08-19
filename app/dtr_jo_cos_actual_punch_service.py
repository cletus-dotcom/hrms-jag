"""Restore DTR In/Out from biometric punches instead of generated 8 AM / 5 PM (or 7 AM / 6 PM) stamps."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Iterable

from app import db
from app.models import DailyTimeRecord, DtrUploadBatch, DtrUploadPunch, Employee

# Generated duty stamps (Prepare DTR Period). Seconds are always 00 on these.
DEFAULT_DUTY_IN = frozenset({(7, 0), (8, 0)})
DEFAULT_DUTY_OUT = frozenset({(17, 0), (18, 0)})
RESTORE_SLOTS = ('am_in', 'am_out', 'pm_in', 'pm_out')


def employee_is_jo_cos(emp) -> bool:
    text = ((getattr(emp, 'status_of_appointment', None) or '') + ' ' + (
        getattr(emp, 'nature_of_appointment', None) or ''
    )).strip().lower()
    return ('job order' in text) or ('contract of service' in text) or ('cos' in text) or text == 'jo'


def is_generated_duty_in(t) -> bool:
    return bool(t) and (t.hour, t.minute) in DEFAULT_DUTY_IN and int(getattr(t, 'second', 0) or 0) == 0


def is_generated_duty_out(t) -> bool:
    return bool(t) and (t.hour, t.minute) in DEFAULT_DUTY_OUT and int(getattr(t, 'second', 0) or 0) == 0


def is_generated_duty_stamp_for_slot(existing, slot: str) -> bool:
    if existing is None:
        return True
    if slot == 'am_in':
        return is_generated_duty_in(existing)
    if slot == 'pm_out':
        return is_generated_duty_out(existing)
    return False


def parse_time_from_source_timestamp(raw: str | None, fallback: time | None = None) -> time | None:
    """Prefer HH:MM:SS from biometric source_timestamp; else fallback punch_time."""
    s = (raw or '').strip()
    if s:
        token = s.split()[-1] if ' ' in s else s
        token = token.replace('T', ' ').split()[-1]
        token = token[:8] if len(token) >= 8 and token[2] == ':' else token
        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                return datetime.strptime(token, fmt).time()
            except ValueError:
                continue
    return fallback


def punch_actual_time(punch) -> time | None:
    return parse_time_from_source_timestamp(
        getattr(punch, 'source_timestamp', None),
        getattr(punch, 'punch_time', None),
    )


def merge_prefer_actual_over_duty_stamp(existing, incoming, slot: str):
    """Fill blanks; replace generated 7/8 AM In or 5/6 PM Out when a biometric time exists."""
    if incoming is None:
        return existing
    if is_generated_duty_stamp_for_slot(existing, slot):
        return incoming
    return existing


def _chosen_punch_for_slot(punches: list[DtrUploadPunch], slot: str) -> DtrUploadPunch | None:
    assigned = [
        p for p in punches
        if (p.assigned_slot or '').strip() == slot
    ]
    if assigned:
        return max(assigned, key=lambda p: (p.is_selected, p.sequence_no or 0, p.id or 0))
    selected = [
        p for p in punches
        if p.is_selected and (p.slot or '').strip() == slot
    ]
    if selected:
        return max(selected, key=lambda p: (p.sequence_no or 0, p.id or 0))
    return None


def _times_equal(a, b) -> bool:
    return a == b


def _is_minute_truncated_of(current, actual) -> bool:
    """True when DB kept HH:MM:00 and biometric still has seconds."""
    if current is None or actual is None:
        return False
    return (
        current.hour == actual.hour
        and current.minute == actual.minute
        and int(getattr(current, 'second', 0) or 0) == 0
        and int(getattr(actual, 'second', 0) or 0) > 0
    )


def row_is_orphan_generated_duty_stamp(rec) -> bool:
    """True when DTR has only generated 7/8 AM In and 5/6 PM Out (no break punches)."""
    if rec is None:
        return False
    if getattr(rec, 'am_out', None) or getattr(rec, 'pm_in', None):
        return False
    return is_generated_duty_in(getattr(rec, 'am_in', None)) and is_generated_duty_out(
        getattr(rec, 'pm_out', None)
    )


def restore_actual_dtr_punches(
    start_d: date | None = None,
    end_d: date | None = None,
    employees: Iterable[Employee] | None = None,
    *,
    clear_orphan_stamps: bool = True,
) -> dict:
    """
    Replace generated In/Out stamps with selected biometric punches (plantilla and JO/COS).

    Also writes seconds back onto slots that were stored as HH:MM:00 from the same punch.
    Clears leftover 8 AM / 5 PM (or 7 AM / 6 PM) stamps on days with no biometric punches
    for plantilla and JO/COS (no CSC present-day defaults).
    Returns counts: employees, days, slots, cleared.
    """
    emp_list = list(employees) if employees is not None else (
        Employee.query.filter(Employee.status == 'active').all()
    )
    if not emp_list:
        return {'employees': 0, 'days': 0, 'slots': 0, 'cleared': 0}

    emp_ids = [e.id for e in emp_list]
    punch_q = (
        DtrUploadPunch.query
        .join(DtrUploadBatch, DtrUploadBatch.id == DtrUploadPunch.batch_id)
        .filter(DtrUploadBatch.status == 'active')
        .filter(DtrUploadPunch.employee_id.in_(emp_ids))
    )
    dtr_q = DailyTimeRecord.query.filter(DailyTimeRecord.employee_id.in_(emp_ids))
    if start_d is not None:
        punch_q = punch_q.filter(DtrUploadPunch.record_date >= start_d)
        dtr_q = dtr_q.filter(DailyTimeRecord.record_date >= start_d)
    if end_d is not None:
        punch_q = punch_q.filter(DtrUploadPunch.record_date <= end_d)
        dtr_q = dtr_q.filter(DailyTimeRecord.record_date <= end_d)

    punches_by_day: dict[tuple[int, date], list[DtrUploadPunch]] = {}
    for p in punch_q.all():
        punches_by_day.setdefault((p.employee_id, p.record_date), []).append(p)

    dtr_by_day = {(r.employee_id, r.record_date): r for r in dtr_q.all()}

    days_changed = 0
    slots_changed = 0
    days_cleared = 0
    employees_changed: set[int] = set()

    for key, day_punches in punches_by_day.items():
        rec = dtr_by_day.get(key)
        if rec is None:
            continue
        changed = False
        for slot in RESTORE_SLOTS:
            chosen = _chosen_punch_for_slot(day_punches, slot)
            if chosen is None:
                continue
            actual = punch_actual_time(chosen)
            if actual is None:
                continue
            current = getattr(rec, slot)
            replace = False
            if slot in ('am_in', 'pm_out'):
                replace = current is None or is_generated_duty_stamp_for_slot(current, slot)
            else:
                replace = current is None
            if not replace:
                replace = _is_minute_truncated_of(current, actual)
            if not replace or _times_equal(current, actual):
                continue
            setattr(rec, slot, actual)
            slots_changed += 1
            changed = True
        if changed:
            days_changed += 1
            employees_changed.add(key[0])

    # Days with no biometric log must not keep generated 8-5 / 7-6 as if present.
    if clear_orphan_stamps:
        for key, rec in dtr_by_day.items():
            if not row_is_orphan_generated_duty_stamp(rec):
                continue
            if punches_by_day.get(key):
                continue
            rec.am_in = None
            rec.pm_out = None
            days_cleared += 1
            employees_changed.add(key[0])

    return {
        'employees': len(employees_changed),
        'days': days_changed,
        'slots': slots_changed,
        'cleared': days_cleared,
    }


restore_jo_cos_actual_punches = restore_actual_dtr_punches
