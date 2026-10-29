"""Workweek arrangement segments for DTR generation (4-day / 5-day, multiple per period)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Iterable

from flask import g

from app import db
from app.models import DtrWorkArrangementSetting, Employee, FlexibleWorktime

def flexi_coverage_keys(
    employee_ids: Iterable[int],
    period_start: date,
    period_end: date,
) -> set[tuple[int, date]]:
    """(employee_id, calendar date) covered by the Flexible Worktime quincena list."""
    ids = [int(x) for x in employee_ids]
    if not ids:
        return set()
    rows = (
        FlexibleWorktime.query
        .filter(FlexibleWorktime.employee_id.in_(ids))
        .filter(FlexibleWorktime.date_start <= period_end)
        .filter(FlexibleWorktime.date_end >= period_start)
        .all()
    )
    covered: set[tuple[int, date]] = set()
    for row in rows:
        d = max(row.date_start, period_start)
        last = min(row.date_end, period_end)
        while d <= last:
            covered.add((row.employee_id, d))
            d += timedelta(days=1)
    return covered


DTR_DUTY_MODELS = {
    '5day': {'am_in': (8, 0), 'am_out': (12, 0), 'pm_in': (13, 0), 'pm_out': (17, 0)},
    '4day': {'am_in': (7, 0), 'am_out': (12, 0), 'pm_in': (13, 0), 'pm_out': (18, 0)},
}

VALID_APPLIES_TO = frozenset({'all', 'regular', 'jo_cos'})


@dataclass(frozen=True)
class WorkweekSegment:
    model_code: str
    applies_to: str
    start_date: date
    end_date: date

    def clipped(self, period_start: date, period_end: date) -> WorkweekSegment:
        return WorkweekSegment(
            model_code=self.model_code,
            applies_to=self.applies_to,
            start_date=max(self.start_date, period_start),
            end_date=min(self.end_date, period_end),
        )


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw.strip(), '%Y-%m-%d').date()


def _applies_groups_overlap(a: str, b: str) -> bool:
    a = (a or 'all').strip().lower()
    b = (b or 'all').strip().lower()
    if a == 'all' or b == 'all':
        return True
    return a == b


def _ranges_overlap(s1: date, e1: date, s2: date, e2: date) -> bool:
    return s1 <= e2 and s2 <= e1


def parse_segments_from_form(
    form,
    period_start: date,
    period_end: date,
) -> tuple[list[WorkweekSegment], str | None]:
    """
    Parse workweek segments from POST form.
    Prefers workweek_segments_json; falls back to legacy single-segment fields.
    Returns (segments, error_message).
    """
    raw_json = (form.get('workweek_segments_json') or '').strip()
    segments: list[WorkweekSegment] = []

    if raw_json:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return [], 'Invalid workweek segments payload.'
        if not isinstance(payload, list):
            return [], 'Workweek segments must be a list.'
        for idx, item in enumerate(payload):
            if not isinstance(item, dict):
                return [], f'Workweek segment {idx + 1} is invalid.'
            model = (item.get('model') or item.get('model_code') or '').strip().lower()
            applies = (item.get('applies_to') or 'all').strip().lower()
            start_raw = (item.get('start_date') or '').strip()
            end_raw = (item.get('end_date') or '').strip()
            if not model:
                continue
            if model not in DTR_DUTY_MODELS:
                return [], f'Workweek segment {idx + 1}: invalid model.'
            if applies not in VALID_APPLIES_TO:
                return [], f'Workweek segment {idx + 1}: invalid applies-to group.'
            if not start_raw or not end_raw:
                return [], f'Workweek segment {idx + 1}: start and end dates are required.'
            try:
                start_d = _parse_date(start_raw)
                end_d = _parse_date(end_raw)
            except ValueError:
                return [], f'Workweek segment {idx + 1}: invalid date format.'
            if end_d < start_d:
                return [], f'Workweek segment {idx + 1}: end date must be on or after start date.'
            segments.append(WorkweekSegment(model, applies, start_d, end_d))
    else:
        model = (form.get('arrangement_model') or '').strip().lower()
        applies = (form.get('arrangement_applies_to') or 'all').strip().lower()
        start_raw = (form.get('arrangement_start_date') or '').strip()
        end_raw = (form.get('arrangement_end_date') or '').strip()
        if model:
            if model not in DTR_DUTY_MODELS:
                return [], 'Invalid work arrangement model.'
            if applies not in VALID_APPLIES_TO:
                return [], 'Invalid work arrangement target group.'
            if not start_raw or not end_raw:
                return [], 'Work arrangement start and end dates are required.'
            try:
                start_d = _parse_date(start_raw)
                end_d = _parse_date(end_raw)
            except ValueError:
                return [], 'Invalid work arrangement date format.'
            if end_d < start_d:
                return [], 'Work arrangement end date must be on or after start date.'
            segments.append(WorkweekSegment(model, applies, start_d, end_d))

    return validate_segments(segments, period_start, period_end)


def validate_segments(
    segments: list[WorkweekSegment],
    period_start: date,
    period_end: date,
) -> tuple[list[WorkweekSegment], str | None]:
    if not segments:
        return [], None

    for i, seg in enumerate(segments):
        if seg.start_date < period_start or seg.end_date > period_end:
            return [], (
                f'Workweek segment {i + 1} ({seg.start_date} to {seg.end_date}) '
                f'must fall within the generation period ({period_start} to {period_end}).'
            )
        for j in range(i + 1, len(segments)):
            other = segments[j]
            if _applies_groups_overlap(seg.applies_to, other.applies_to):
                if _ranges_overlap(seg.start_date, seg.end_date, other.start_date, other.end_date):
                    return [], (
                        f'Workweek segments {i + 1} and {j + 1} overlap for the same employee group '
                        f'({seg.start_date}–{seg.end_date} vs {other.start_date}–{other.end_date}).'
                    )
    return segments, None


def save_workweek_segments(
    segments: Iterable[WorkweekSegment],
    created_by_user_id: int | None,
) -> list[DtrWorkArrangementSetting]:
    saved: list[DtrWorkArrangementSetting] = []
    for seg in segments:
        row = DtrWorkArrangementSetting(
            model_code=seg.model_code,
            applies_to=seg.applies_to,
            start_date=seg.start_date,
            end_date=seg.end_date,
            notes='Set from DTR Generate (quincena)',
            created_by_user_id=created_by_user_id,
        )
        db.session.add(row)
        db.session.flush()
        saved.append(row)
    return saved


def apply_workweek_segments_to_dtr(
    segments: Iterable[WorkweekSegment],
    employees: list[Employee],
    period_start: date,
    period_end: date,
    special_dates: set[date],
    *,
    find_row: Callable,
    skip_stamp: Callable,
    matches_applies: Callable,
    skip_duty_stamp: Callable | None = None,
    is_jo_only: Callable | None = None,
) -> None:
    """Apply REST DAY (4-day Fridays) from workweek segments. Does not stamp In/Out times.

    Default 8:00/17:00 and 7:00/18:00 present-day times are not written; biometric
    upload supplies actual punches. Flexible Worktime list dates skip Friday REST DAY.
    ``skip_duty_stamp`` / ``is_jo_only`` are ignored (kept for callers).
    """
    _ = skip_duty_stamp or is_jo_only
    flexi_covered = flexi_coverage_keys(
        [e.id for e in employees], period_start, period_end
    )
    for seg in segments:
        clipped = seg.clipped(period_start, period_end)
        if clipped.end_date < clipped.start_date:
            continue
        if seg.model_code == '4day':
            _apply_4day_segment(
                clipped, employees, special_dates,
                find_row=find_row, skip_stamp=skip_stamp,
                matches_applies=matches_applies,
                flexi_covered=flexi_covered,
            )


def _apply_4day_segment(
    seg: WorkweekSegment,
    employees: list[Employee],
    special_dates: set[date],
    *,
    find_row: Callable,
    skip_stamp: Callable,
    matches_applies: Callable,
    skip_duty_stamp: Callable | None = None,
    flexi_covered: set[tuple[int, date]] | None = None,
) -> None:
    _ = skip_duty_stamp
    flexi_covered = flexi_covered or set()
    db.session.flush()
    d = seg.start_date
    while d <= seg.end_date:
        if d.weekday() == 4 and d not in special_dates:
            for emp in employees:
                if not matches_applies(emp, seg.applies_to):
                    continue
                if (emp.id, d) in flexi_covered or getattr(emp, 'flexible_worktime', False):
                    continue
                rec = find_row(emp, d)
                if not rec or skip_stamp(rec, d, special_dates):
                    continue
                if not any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]):
                    rec.remarks = 'REST DAY'
        d += timedelta(days=1)


def format_segments_flash_message(segments: list[WorkweekSegment]) -> str:
    if not segments:
        return ''
    parts = []
    for seg in segments:
        parts.append(
            f'{seg.model_code.upper()} ({seg.start_date} to {seg.end_date}, {seg.applies_to})'
        )
    if len(parts) == 1:
        return f'Work arrangement saved: {parts[0]}.'
    return f'Work arrangements saved ({len(parts)} segments): ' + '; '.join(parts)


def _employee_is_regular_for_arrangement(emp: Employee) -> bool:
    soa = (emp.status_of_appointment or '').strip().lower()
    if soa:
        return ('permanent' in soa) or ('regular' in soa)
    text = ((emp.status_of_appointment or '') + ' ' + (emp.nature_of_appointment or '')).strip().lower()
    is_jo_cos = (
        ('job order' in text)
        or ('contract of service' in text)
        or ('cos' in text)
        or text == 'jo'
    )
    return not is_jo_cos


def work_arrangement_settings_cached() -> list[DtrWorkArrangementSetting]:
    """All workweek segments, newest first. One query per request."""
    try:
        cache = getattr(g, '_dtr_work_arrangement_settings', None)
    except RuntimeError:
        cache = None
    if cache is None:
        cache = (
            DtrWorkArrangementSetting.query
            .order_by(DtrWorkArrangementSetting.created_at.desc(), DtrWorkArrangementSetting.id.desc())
            .all()
        )
        try:
            g._dtr_work_arrangement_settings = cache
        except RuntimeError:
            pass
    return cache


def work_arrangement_settings_covering(d: date) -> list[DtrWorkArrangementSetting]:
    return [
        row for row in work_arrangement_settings_cached()
        if row.start_date and row.end_date and row.start_date <= d <= row.end_date
    ]


def arrangement_model_code_for_date(emp: Employee, d: date) -> str:
    """
    Effective duty model for emp on calendar date ``d``.

    Returns ``4day``, ``5day``, or ``flex`` (flexible worktime).
    Defaults to ``5day`` when no segment covers the date.
    """
    if bool(getattr(emp, 'flexible_worktime', False)):
        return 'flex'
    from app.workweek_calendar_service import calendar_model_code_for_date
    cal = calendar_model_code_for_date(d)
    if cal in ('4day', '5day'):
        return cal
    is_regular = _employee_is_regular_for_arrangement(emp)
    rows = work_arrangement_settings_covering(d)
    for row in rows:
        applies = (row.applies_to or 'all').strip().lower()
        if applies == 'all' or (applies == 'regular' and is_regular) or (applies == 'jo_cos' and not is_regular):
            return ((row.model_code or '5day').strip().lower() or '5day')
    return '5day'
