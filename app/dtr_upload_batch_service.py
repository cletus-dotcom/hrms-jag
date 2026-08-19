"""DTR upload batch logging, quincena validation, undo, and duplicate-punch flags."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from flask_login import current_user

from app import db
from app.dtr_parse import EVENT_CODE_TO_SLOT, duplicate_slots_from_punches
from app.models import (
    DailyTimeRecord,
    DtrUploadBatch,
    DtrUploadBatchItem,
    DtrUploadPunch,
    DtrUploadPunchFlag,
)

# Extra calendar days accepted on each side of a quincena when uploading DTR logs
# (night-shift / flexitime punches often fall on the adjacent day).
DTR_UPLOAD_QUINCENA_BUFFER_DAYS = 1

SLOT_LABELS = {
    'am_in': 'CHECK-IN',
    'am_out': 'BREAK-OUT',
    'pm_in': 'BREAK-IN',
    'pm_out': 'CHECK-OUT',
}

FLAG_STATUS_OPEN = 'open'
FLAG_STATUS_RESOLVED = 'resolved'
FLAG_STATUS_INACTIVE = 'inactive'


def quincena_core_date_range(year: int, month: int, quincena: str) -> tuple[date, date]:
    """Inclusive start/end for the payroll quincena itself."""
    if quincena == '2':
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 16), date(year, month, last_day)
    return date(year, month, 1), date(year, month, 15)


def quincena_upload_accept_range(
    year: int,
    month: int,
    quincena: str,
    *,
    buffer_days: int = DTR_UPLOAD_QUINCENA_BUFFER_DAYS,
) -> tuple[date, date, date, date]:
    """
    Return (accept_start, accept_end, core_start, core_end).
    accept_* widens the core quincena by buffer_days on each side for upload filtering.
    """
    core_start, core_end = quincena_core_date_range(year, month, quincena)
    buf = max(0, int(buffer_days))
    return (
        core_start - timedelta(days=buf),
        core_end + timedelta(days=buf),
        core_start,
        core_end,
    )


def dates_outside_quincena(row_dates: set[date], start_d: date, end_d: date) -> list[date]:
    return sorted(d for d in row_dates if d < start_d or d > end_d)


def snapshot_dtr_row(dtr: DailyTimeRecord) -> dict:
    return {
        'before_am_in': dtr.am_in,
        'before_am_out': dtr.am_out,
        'before_pm_in': dtr.pm_in,
        'before_pm_out': dtr.pm_out,
        'before_remarks': dtr.remarks,
        'before_undertime_hrs': dtr.undertime_hrs,
        'before_undertime_mins': dtr.undertime_mins,
    }


def log_batch_item(batch: DtrUploadBatch, *, employee_id: int, record_date: date, action: str,
                   dtr_record_id: int, before: dict | None = None) -> DtrUploadBatchItem:
    before = before or {}
    item = DtrUploadBatchItem(
        batch_id=batch.id,
        employee_id=employee_id,
        record_date=record_date,
        action=action,
        dtr_record_id=dtr_record_id,
        before_am_in=before.get('before_am_in'),
        before_am_out=before.get('before_am_out'),
        before_pm_in=before.get('before_pm_in'),
        before_pm_out=before.get('before_pm_out'),
        before_remarks=before.get('before_remarks'),
        before_undertime_hrs=before.get('before_undertime_hrs'),
        before_undertime_mins=before.get('before_undertime_mins'),
    )
    db.session.add(item)
    return item


def create_upload_batch(*, quincena: str, filename: str, record_count: int) -> DtrUploadBatch:
    batch = DtrUploadBatch(
        uploaded_by_user_id=getattr(current_user, 'id', None),
        uploaded_by_username=(getattr(current_user, 'username', None) or 'unknown'),
        quincena=quincena,
        filename=filename or '',
        record_count=record_count,
        status='active',
    )
    db.session.add(batch)
    db.session.flush()
    return batch


def can_undo_dtr_batch(batch: DtrUploadBatch | None, user=None) -> bool:
    if batch is None:
        return False
    if (batch.status or '').strip().lower() != 'active':
        return False
    from app.portal_auth import is_portal_admin_session
    from app.portal_config import is_admin_or_hr_role
    if is_portal_admin_session():
        return True
    user = user or current_user
    if is_admin_or_hr_role(getattr(user, 'role', None)):
        return True
    uid = getattr(user, 'id', None)
    if uid is not None and batch.uploaded_by_user_id == uid:
        return True
    uname = (getattr(user, 'username', None) or '').strip()
    if uname and batch.uploaded_by_username == uname:
        return True
    return False


def list_recent_dtr_upload_batches(user=None, limit: int = 20) -> list[DtrUploadBatch]:
    user = user or current_user
    role = (getattr(user, 'role', None) or '').strip().lower()
    q = DtrUploadBatch.query.order_by(DtrUploadBatch.uploaded_at.desc())
    if role not in ('admin', 'hr'):
        uid = getattr(user, 'id', None)
        uname = (getattr(user, 'username', None) or '').strip()
        if uid is not None:
            q = q.filter(DtrUploadBatch.uploaded_by_user_id == uid)
        elif uname:
            q = q.filter(DtrUploadBatch.uploaded_by_username == uname)
        else:
            return []
    return q.limit(limit).all()


def _parse_punch_time(value) -> time | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    s = str(value or '').strip()
    if not s:
        return None
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def persist_upload_punches_and_flags(
    batch: DtrUploadBatch,
    *,
    employee_id: int,
    badge_employee_id: str,
    record_date: date,
    punches: list[dict] | None,
    selected_times: dict[str, time | None] | None = None,
) -> int:
    """
    Persist raw punches for one accepted employee-day and open duplicate-slot flags.
    Returns number of open flags created.
    """
    punches = punches or []
    if not punches:
        return 0
    _ = selected_times  # reserved for callers; last punch in source order marks is_selected
    created_flags = 0
    punch_rows: list[DtrUploadPunch] = []
    last_by_slot: dict[str, DtrUploadPunch] = {}
    for p in punches:
        slot = (p.get('slot') or EVENT_CODE_TO_SLOT.get(p.get('event_code')) or '').strip()
        if slot not in SLOT_LABELS:
            continue
        punch_time = _parse_punch_time(p.get('time'))
        row = DtrUploadPunch(
            batch_id=batch.id,
            employee_id=employee_id,
            badge_employee_id=(badge_employee_id or '').strip(),
            record_date=record_date,
            event_code=int(p.get('event_code') if p.get('event_code') is not None else 0),
            slot=slot,
            punch_time=punch_time,
            source_timestamp=(p.get('source_timestamp') or '').strip() or (
                punch_time.strftime('%H:%M') if punch_time else ''
            ),
            line_no=int(p.get('line_no') or 0),
            sequence_no=int(p.get('sequence_no') or 0),
            is_selected=False,
            assigned_slot=None,
        )
        punch_rows.append(row)
        last_by_slot[slot] = row
        db.session.add(row)

    for row in last_by_slot.values():
        row.is_selected = True
        row.assigned_slot = row.slot

    db.session.flush()

    dup_slots = duplicate_slots_from_punches([
        {
            'slot': r.slot,
            'time': r.punch_time.strftime('%H:%M') if r.punch_time else '',
            'event_code': r.event_code,
            'source_timestamp': r.source_timestamp,
            'line_no': r.line_no,
            'sequence_no': r.sequence_no,
        }
        for r in punch_rows
    ])
    for slot, items in dup_slots.items():
        flag = DtrUploadPunchFlag(
            batch_id=batch.id,
            employee_id=employee_id,
            record_date=record_date,
            slot=slot,
            punch_count=len(items),
            status=FLAG_STATUS_OPEN,
        )
        db.session.add(flag)
        created_flags += 1
    return created_flags


def count_open_punch_flags(batch_id: int | None = None, *, employee_id: int | None = None,
                           start_d: date | None = None, end_d: date | None = None) -> int:
    q = DtrUploadPunchFlag.query.filter(DtrUploadPunchFlag.status == FLAG_STATUS_OPEN)
    q = q.join(DtrUploadBatch, DtrUploadBatch.id == DtrUploadPunchFlag.batch_id).filter(
        DtrUploadBatch.status == 'active'
    )
    if batch_id is not None:
        q = q.filter(DtrUploadPunchFlag.batch_id == batch_id)
    if employee_id is not None:
        q = q.filter(DtrUploadPunchFlag.employee_id == employee_id)
    if start_d is not None:
        q = q.filter(DtrUploadPunchFlag.record_date >= start_d)
    if end_d is not None:
        q = q.filter(DtrUploadPunchFlag.record_date <= end_d)
    return q.count()


def open_punch_flags_by_date(employee_id: int, start_d: date, end_d: date) -> dict[date, list[DtrUploadPunchFlag]]:
    """Open flags for an employee in a date range, keyed by record_date."""
    rows = (
        DtrUploadPunchFlag.query
        .join(DtrUploadBatch, DtrUploadBatch.id == DtrUploadPunchFlag.batch_id)
        .filter(DtrUploadBatch.status == 'active')
        .filter(DtrUploadPunchFlag.status == FLAG_STATUS_OPEN)
        .filter(DtrUploadPunchFlag.employee_id == employee_id)
        .filter(DtrUploadPunchFlag.record_date >= start_d)
        .filter(DtrUploadPunchFlag.record_date <= end_d)
        .order_by(DtrUploadPunchFlag.record_date.asc(), DtrUploadPunchFlag.slot.asc())
        .all()
    )
    out: dict[date, list[DtrUploadPunchFlag]] = {}
    for flag in rows:
        out.setdefault(flag.record_date, []).append(flag)
    return out


def reviewable_punch_flags_by_date(
    employee_id: int,
    start_d: date,
    end_d: date,
) -> dict[date, list[DtrUploadPunchFlag]]:
    """Open or resolved flags (re-assignable) for DTR badge links, keyed by record_date."""
    rows = (
        DtrUploadPunchFlag.query
        .join(DtrUploadBatch, DtrUploadBatch.id == DtrUploadPunchFlag.batch_id)
        .filter(DtrUploadBatch.status == 'active')
        .filter(DtrUploadPunchFlag.status.in_((FLAG_STATUS_OPEN, FLAG_STATUS_RESOLVED)))
        .filter(DtrUploadPunchFlag.employee_id == employee_id)
        .filter(DtrUploadPunchFlag.record_date >= start_d)
        .filter(DtrUploadPunchFlag.record_date <= end_d)
        .order_by(DtrUploadPunchFlag.record_date.asc(), DtrUploadPunchFlag.slot.asc())
        .all()
    )
    out: dict[date, list[DtrUploadPunchFlag]] = {}
    for flag in rows:
        out.setdefault(flag.record_date, []).append(flag)
    return out


def list_punch_flags_for_batch(batch_id: int) -> list[DtrUploadPunchFlag]:
    return (
        DtrUploadPunchFlag.query
        .filter_by(batch_id=batch_id)
        .order_by(
            DtrUploadPunchFlag.status.asc(),
            DtrUploadPunchFlag.record_date.asc(),
            DtrUploadPunchFlag.employee_id.asc(),
            DtrUploadPunchFlag.slot.asc(),
        )
        .all()
    )


def punches_for_flag(flag: DtrUploadPunchFlag) -> list[DtrUploadPunch]:
    return (
        DtrUploadPunch.query
        .filter_by(
            batch_id=flag.batch_id,
            employee_id=flag.employee_id,
            record_date=flag.record_date,
            slot=flag.slot,
        )
        .order_by(DtrUploadPunch.sequence_no.asc(), DtrUploadPunch.line_no.asc(), DtrUploadPunch.id.asc())
        .all()
    )


def punches_for_employee_day(
    batch_id: int,
    employee_id: int,
    record_date: date,
) -> list[DtrUploadPunch]:
    """All retained punches for one upload batch / employee / date (any source slot)."""
    return (
        DtrUploadPunch.query
        .filter_by(
            batch_id=batch_id,
            employee_id=employee_id,
            record_date=record_date,
        )
        .order_by(DtrUploadPunch.sequence_no.asc(), DtrUploadPunch.line_no.asc(), DtrUploadPunch.id.asc())
        .all()
    )


def punches_by_employee_day_for_batch(
    batch_id: int,
    *,
    employee_ids: set[int] | list[int] | None = None,
) -> dict[tuple[int, date], list[DtrUploadPunch]]:
    """
    Bulk-load retained punches for a batch, keyed by (employee_id, record_date).
    Avoids per-day N+1 queries on the punch-flags review page.
    """
    q = DtrUploadPunch.query.filter_by(batch_id=batch_id)
    if employee_ids is not None:
        ids = list(employee_ids)
        if not ids:
            return {}
        q = q.filter(DtrUploadPunch.employee_id.in_(ids))
    rows = q.order_by(
        DtrUploadPunch.employee_id.asc(),
        DtrUploadPunch.record_date.asc(),
        DtrUploadPunch.sequence_no.asc(),
        DtrUploadPunch.line_no.asc(),
        DtrUploadPunch.id.asc(),
    ).all()
    out: dict[tuple[int, date], list[DtrUploadPunch]] = {}
    for punch in rows:
        out.setdefault((punch.employee_id, punch.record_date), []).append(punch)
    return out


def open_flags_for_employee_day(
    batch_id: int,
    employee_id: int,
    record_date: date,
) -> list[DtrUploadPunchFlag]:
    return (
        DtrUploadPunchFlag.query
        .filter_by(
            batch_id=batch_id,
            employee_id=employee_id,
            record_date=record_date,
            status=FLAG_STATUS_OPEN,
        )
        .order_by(DtrUploadPunchFlag.slot.asc())
        .all()
    )


def editable_flags_for_employee_day(
    batch_id: int,
    employee_id: int,
    record_date: date,
) -> list[DtrUploadPunchFlag]:
    """Open or resolved flags for a day (resolved may be re-assigned)."""
    return (
        DtrUploadPunchFlag.query
        .filter(
            DtrUploadPunchFlag.batch_id == batch_id,
            DtrUploadPunchFlag.employee_id == employee_id,
            DtrUploadPunchFlag.record_date == record_date,
            DtrUploadPunchFlag.status.in_((FLAG_STATUS_OPEN, FLAG_STATUS_RESOLVED)),
        )
        .order_by(DtrUploadPunchFlag.slot.asc())
        .all()
    )


def deactivate_punch_flags_for_batch(batch: DtrUploadBatch) -> int:
    """Mark all punch flags for a batch inactive (used on undo)."""
    flags = DtrUploadPunchFlag.query.filter_by(batch_id=batch.id).all()
    n = 0
    for flag in flags:
        if flag.status != FLAG_STATUS_INACTIVE:
            flag.status = FLAG_STATUS_INACTIVE
            n += 1
    return n


def undo_dtr_upload_batch(batch_id: int, user=None) -> tuple[bool, str, dict]:
    """
    Reverse an upload batch. Returns (ok, message, stats).
    stats: restored, deleted, skipped
    """
    user = user or current_user
    batch = DtrUploadBatch.query.get(batch_id)
    if not batch:
        return False, 'Upload batch not found.', {}
    if not can_undo_dtr_batch(batch, user):
        return False, 'You do not have permission to undo this upload.', {}
    if batch.status != 'active':
        return False, 'This upload has already been undone.', {}

    items = (
        DtrUploadBatchItem.query
        .filter_by(batch_id=batch.id)
        .order_by(DtrUploadBatchItem.id.asc())
        .all()
    )
    deleted = 0
    restored = 0
    skipped = 0

    for item in items:
        dtr = DailyTimeRecord.query.get(item.dtr_record_id) if item.dtr_record_id else None
        if (
            dtr is None
            or dtr.employee_id != item.employee_id
            or dtr.record_date != item.record_date
        ):
            skipped += 1
            continue
        if item.action == 'created':
            db.session.delete(dtr)
            deleted += 1
        elif item.action == 'merged':
            dtr.am_in = item.before_am_in
            dtr.am_out = item.before_am_out
            dtr.pm_in = item.before_pm_in
            dtr.pm_out = item.before_pm_out
            dtr.remarks = item.before_remarks
            dtr.undertime_hrs = item.before_undertime_hrs if item.before_undertime_hrs is not None else 0
            dtr.undertime_mins = item.before_undertime_mins if item.before_undertime_mins is not None else 0
            restored += 1
        else:
            skipped += 1

    flags_deactivated = deactivate_punch_flags_for_batch(batch)
    batch.status = 'undone'
    batch.undone_at = datetime.utcnow()
    batch.undone_by_user_id = getattr(user, 'id', None)
    db.session.commit()

    stats = {
        'deleted': deleted,
        'restored': restored,
        'skipped': skipped,
        'flags_deactivated': flags_deactivated,
    }
    msg = f'Upload batch #{batch.id} undone: {deleted} row(s) removed, {restored} row(s) restored.'
    if skipped:
        msg += f' {skipped} row(s) skipped (already changed or removed).'
    if flags_deactivated:
        msg += f' {flags_deactivated} punch flag(s) deactivated.'
    return True, msg, stats


def apply_selected_punch_to_dtr(
    flag: DtrUploadPunchFlag,
    punch: DtrUploadPunch,
    *,
    user=None,
    ensure_batch_item: bool = True,
) -> tuple[bool, str]:
    """
    Backward-compatible single-slot resolve: assign ``punch`` to ``flag.slot`` and keep other slots.
    Prefer ``apply_day_punch_assignments`` for four-slot review.
    """
    choices = {slot: 'keep' for slot in SLOT_LABELS}
    choices[flag.slot] = punch.id
    return apply_day_punch_assignments(
        batch_id=flag.batch_id,
        employee_id=flag.employee_id,
        record_date=flag.record_date,
        slot_choices=choices,
        user=user,
        ensure_batch_item=ensure_batch_item,
    )


def apply_day_punch_assignments(
    *,
    batch_id: int,
    employee_id: int,
    record_date: date,
    slot_choices: dict[str, int | str],
    user=None,
    ensure_batch_item: bool = True,
) -> tuple[bool, str]:
    """
    Atomically assign retained punches across the four DTR slots for one employee-day.

    ``slot_choices`` maps each of am_in/am_out/pm_in/pm_out to:
      - ``'keep'`` — leave the current DailyTimeRecord value
      - ``'clear'`` — blank the slot
      - punch id (int) — use that retained punch's time (each punch may be used once)

    Extra punches may remain unused. Resolves open duplicate flags for the day and
    allows re-assignment of already-resolved flags (same Clear / Keep / punch options).
    Returns (ok, message). Caller is responsible for commit.
    """
    user = user or current_user
    batch = DtrUploadBatch.query.get(batch_id)
    if not batch or (batch.status or '').strip().lower() != 'active':
        return False, 'Upload batch is not active.'

    if set(slot_choices.keys()) != set(SLOT_LABELS.keys()):
        return False, 'All four time slots must be included in the assignment.'

    day_punches = punches_for_employee_day(batch_id, employee_id, record_date)
    punch_by_id = {p.id: p for p in day_punches}
    if not day_punches:
        return False, 'No retained punches found for this employee day.'

    chosen_punch_ids: list[int] = []
    resolved_times: dict[str, time | None] = {}
    dtr = DailyTimeRecord.query.filter_by(
        employee_id=employee_id,
        record_date=record_date,
    ).first()
    current_times = {
        'am_in': dtr.am_in if dtr else None,
        'am_out': dtr.am_out if dtr else None,
        'pm_in': dtr.pm_in if dtr else None,
        'pm_out': dtr.pm_out if dtr else None,
    }

    for slot in SLOT_LABELS:
        raw = slot_choices.get(slot)
        if raw is None:
            return False, f'Missing assignment for {SLOT_LABELS[slot]}.'
        if isinstance(raw, str):
            choice = raw.strip().lower()
            if choice == 'keep':
                resolved_times[slot] = current_times[slot]
                continue
            if choice == 'clear':
                resolved_times[slot] = None
                continue
            try:
                raw = int(choice)
            except ValueError:
                return False, f'Invalid assignment for {SLOT_LABELS[slot]}.'
        try:
            punch_id = int(raw)
        except (TypeError, ValueError):
            return False, f'Invalid punch selection for {SLOT_LABELS[slot]}.'
        punch = punch_by_id.get(punch_id)
        if punch is None:
            return False, f'Selected punch does not belong to this employee day ({SLOT_LABELS[slot]}).'
        if punch_id in chosen_punch_ids:
            return False, 'Each retained punch can be assigned to only one time slot.'
        chosen_punch_ids.append(punch_id)
        resolved_times[slot] = punch.punch_time

    created = False
    if dtr is None:
        dtr = DailyTimeRecord(employee_id=employee_id, record_date=record_date)
        db.session.add(dtr)
        db.session.flush()
        created = True
        if ensure_batch_item:
            log_batch_item(
                batch,
                employee_id=employee_id,
                record_date=record_date,
                action='created',
                dtr_record_id=dtr.id,
            )
    else:
        before = snapshot_dtr_row(dtr)
        changed = (
            resolved_times['am_in'] != dtr.am_in
            or resolved_times['am_out'] != dtr.am_out
            or resolved_times['pm_in'] != dtr.pm_in
            or resolved_times['pm_out'] != dtr.pm_out
        )
        if changed and ensure_batch_item:
            existing_item = (
                DtrUploadBatchItem.query
                .filter_by(batch_id=batch.id, employee_id=employee_id, record_date=record_date)
                .first()
            )
            if existing_item is None:
                log_batch_item(
                    batch,
                    employee_id=employee_id,
                    record_date=record_date,
                    action='merged',
                    dtr_record_id=dtr.id,
                    before=before,
                )

    dtr.am_in = resolved_times['am_in']
    dtr.am_out = resolved_times['am_out']
    dtr.pm_in = resolved_times['pm_in']
    dtr.pm_out = resolved_times['pm_out']

    prev_assigned = {p.id: (p.assigned_slot or None) for p in day_punches}
    assigned_by_slot: dict[str, int] = {}
    for slot, raw in slot_choices.items():
        if isinstance(raw, str) and raw.strip().lower() in ('keep', 'clear'):
            continue
        try:
            punch_id = int(raw)
        except (TypeError, ValueError):
            continue
        assigned_by_slot[slot] = punch_id

    claimed = set(assigned_by_slot.values())
    for slot, raw in slot_choices.items():
        if not (isinstance(raw, str) and raw.strip().lower() == 'keep'):
            continue
        if slot in assigned_by_slot:
            continue
        # Preserve prior assignment for kept slots when that punch is still unused
        for p in day_punches:
            if prev_assigned.get(p.id) == slot and p.id not in claimed:
                if p.punch_time == resolved_times[slot]:
                    assigned_by_slot[slot] = p.id
                    claimed.add(p.id)
                    break

    for punch in day_punches:
        punch.assigned_slot = None
        punch.is_selected = False
    for slot, punch_id in assigned_by_slot.items():
        punch = punch_by_id[punch_id]
        punch.assigned_slot = slot
        punch.is_selected = True

    now = datetime.utcnow()
    user_id = getattr(user, 'id', None)
    day_flags = editable_flags_for_employee_day(batch_id, employee_id, record_date)
    open_count = 0
    for flag in day_flags:
        was_open = (flag.status or '').strip().lower() == FLAG_STATUS_OPEN
        if was_open:
            open_count += 1
        flag.status = FLAG_STATUS_RESOLVED
        flag.selected_punch_id = assigned_by_slot.get(flag.slot)
        flag.resolved_by_user_id = user_id
        flag.resolved_at = now

    parts = []
    for slot in ('am_in', 'am_out', 'pm_in', 'pm_out'):
        val = resolved_times[slot]
        parts.append(f'{SLOT_LABELS[slot]}={val.strftime("%H:%M") if val else "—"}')
    verb = 'created' if created else 'updated'
    if open_count:
        flag_note = f'Resolved {open_count} open flag(s).'
    elif day_flags:
        flag_note = f'Re-assigned {len(day_flags)} resolved flag(s).'
    else:
        flag_note = 'No punch flags on this day.'
    msg = (
        f'DTR {verb} for {record_date.strftime("%b %d, %Y")}: '
        + ', '.join(parts)
        + f'. {flag_note}'
    )
    return True, msg
