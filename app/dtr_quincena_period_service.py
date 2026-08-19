"""DTR quincena period lifecycle — status, gates, stats, notifications."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import g, url_for

from app import db
from app.models import (
    DtrJustification,
    DtrQuincenaPeriod,
    DtrUploadBatch,
    Employee,
    FlexibleWorktime,
    HrmsNotification,
    JoCosExtendService,
    OvertimeAuthorization,
    User,
)

STATUS_DRAFT = 'draft'
STATUS_UPLOADED = 'uploaded'
STATUS_REVIEW_OPEN = 'review_open'
STATUS_REVIEW_CLOSED = 'review_closed'
STATUS_PROCESSED = 'processed'

STATUS_LABELS = {
    STATUS_DRAFT: 'Draft',
    STATUS_UPLOADED: 'Uploaded',
    STATUS_REVIEW_OPEN: 'Review open',
    STATUS_REVIEW_CLOSED: 'Review closed',
    STATUS_PROCESSED: 'Processed',
}

DEFAULT_REVIEW_DAYS = 3


def _quincena_date_range(year: int, month: int, quincena_half: str) -> tuple[date, date]:
    import calendar
    if str(quincena_half) == '2':
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 16), date(year, month, last_day)
    return date(year, month, 1), date(year, month, 15)


def quincena_period_label(year: int, month: int, quincena_half: str) -> str:
    month_name = date(year, month, 1).strftime('%B %Y')
    half = '1st' if str(quincena_half) == '1' else '2nd'
    return f'{month_name} — {half} quincena'


def quincena_period_value(year: int, month: int, quincena_half: str) -> str:
    return f'{year:04d}-{month:02d}-{quincena_half}'


def parse_quincena_period_value(raw: str) -> tuple[int | None, int | None, str | None]:
    s = (raw or '').strip()
    if not s:
        return None, None, None
    parts = s.split('-')
    if len(parts) != 3:
        return None, None, None
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError:
        return None, None, None
    quincena = parts[2].strip()
    if month < 1 or month > 12 or quincena not in ('1', '2'):
        return None, None, None
    return year, month, quincena


def _period_cache() -> dict:
    try:
        cache = getattr(g, '_dtr_quincena_period_by_key', None)
    except RuntimeError:
        return {}
    if cache is None:
        cache = {}
        try:
            g._dtr_quincena_period_by_key = cache
        except RuntimeError:
            pass
    return cache


def get_period(year: int, month: int, quincena_half: str) -> DtrQuincenaPeriod | None:
    key = (int(year), int(month), str(quincena_half))
    cache = _period_cache()
    if key in cache:
        return cache[key]
    period = DtrQuincenaPeriod.query.filter_by(
        year=year, month=month, quincena_half=str(quincena_half),
    ).first()
    cache[key] = period
    return period


def get_or_create_period(year: int, month: int, quincena_half: str) -> DtrQuincenaPeriod:
    period = get_period(year, month, quincena_half)
    if period:
        return period
    period = DtrQuincenaPeriod(
        year=year,
        month=month,
        quincena_half=str(quincena_half),
        status=STATUS_DRAFT,
    )
    db.session.add(period)
    db.session.flush()
    _period_cache()[(int(year), int(month), str(quincena_half))] = period
    return period


def is_period_locked(period: DtrQuincenaPeriod | None) -> bool:
    return period is not None and (period.status or '').strip() == STATUS_PROCESSED


def can_employee_submit_justification(period: DtrQuincenaPeriod | None) -> bool:
    if period is None:
        return True
    status = (period.status or '').strip()
    if status not in (STATUS_UPLOADED, STATUS_REVIEW_OPEN):
        return False
    if period.review_deadline and date.today() > period.review_deadline:
        return False
    return True


def can_process_worktime(period: DtrQuincenaPeriod | None) -> bool:
    if period is None:
        return True
    return (period.status or '').strip() in (STATUS_REVIEW_CLOSED, STATUS_UPLOADED, STATUS_REVIEW_OPEN)


def mark_uploaded(year: int, month: int, quincena_half: str, user_id: int | None) -> DtrQuincenaPeriod:
    period = get_or_create_period(year, month, quincena_half)
    now = datetime.utcnow()
    period.status = STATUS_REVIEW_OPEN
    period.uploaded_at = now
    period.review_opened_at = now
    period.uploaded_by_user_id = user_id
    if not period.review_deadline:
        period.review_deadline = date.today() + timedelta(days=DEFAULT_REVIEW_DAYS)
    period.review_closed_at = None
    period.processed_at = None
    period.processed_by_user_id = None
    if str(quincena_half) == '2':
        from app import dtr_monthly_submission_service as dtr_monthly
        dtr_monthly.on_q2_uploaded(year, month)
    return period


def open_review(period: DtrQuincenaPeriod, deadline: date | None = None) -> None:
    period.status = STATUS_REVIEW_OPEN
    period.review_opened_at = datetime.utcnow()
    period.review_closed_at = None
    if deadline:
        period.review_deadline = deadline
    elif not period.review_deadline:
        period.review_deadline = date.today() + timedelta(days=DEFAULT_REVIEW_DAYS)


def close_review(period: DtrQuincenaPeriod) -> None:
    period.status = STATUS_REVIEW_CLOSED
    period.review_closed_at = datetime.utcnow()


def mark_processed(period: DtrQuincenaPeriod, user_id: int | None) -> None:
    period.status = STATUS_PROCESSED
    period.processed_at = datetime.utcnow()
    period.processed_by_user_id = user_id


def reopen_for_adjustment(period: DtrQuincenaPeriod) -> None:
    """Allow HR to edit DTR again after processing (before next payroll run)."""
    period.status = STATUS_REVIEW_CLOSED
    period.processed_at = None
    period.processed_by_user_id = None


def period_dashboard_stats(year: int, month: int, quincena_half: str) -> dict:
    start_d, end_d = _quincena_date_range(year, month, quincena_half)
    qval = quincena_period_value(year, month, quincena_half)
    pending_j = (
        DtrJustification.query
        .filter(DtrJustification.record_date >= start_d)
        .filter(DtrJustification.record_date <= end_d)
        .filter(DtrJustification.status.in_(('submitted', 'forwarded')))
        .count()
    )
    approved_j = (
        DtrJustification.query
        .filter(DtrJustification.record_date >= start_d)
        .filter(DtrJustification.record_date <= end_d)
        .filter(DtrJustification.status == 'approved')
        .count()
    )
    rejected_j = (
        DtrJustification.query
        .filter(DtrJustification.record_date >= start_d)
        .filter(DtrJustification.record_date <= end_d)
        .filter(DtrJustification.status == 'rejected')
        .count()
    )
    uploads = DtrUploadBatch.query.filter(
        DtrUploadBatch.quincena == qval,
        DtrUploadBatch.status == 'active',
    ).count()
    flexi = FlexibleWorktime.query.filter_by(
        year=year, month=month, quincena_half=str(quincena_half),
    ).count()
    ot_auth = OvertimeAuthorization.query.filter_by(
        year=year, month=month, quincena_half=str(quincena_half),
    ).count()
    x_service = JoCosExtendService.query.filter_by(
        year=year, month=month, quincena_half=str(quincena_half),
    ).count()
    return {
        'pending_justifications': pending_j,
        'approved_justifications': approved_j,
        'rejected_justifications': rejected_j,
        'active_upload_batches': uploads,
        'flexi_assignments': flexi,
        'overtime_authorizations': ot_auth,
        'x_service_authorizations': x_service,
    }


def _notify_user(user_id: int, title: str, message: str, link_url: str | None = None) -> None:
    if not user_id:
        return
    db.session.add(HrmsNotification(
        user_id=user_id,
        title=title,
        message=message,
        link_url=link_url,
        is_read=False,
    ))


def _active_employee_user_ids() -> list[int]:
    rows = (
        db.session.query(User.id)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.status == 'active')
        .filter(User.is_active.is_(True))
        .all()
    )
    return [r[0] for r in rows]


def _hr_admin_user_ids() -> list[int]:
    rows = (
        User.query.filter(User.is_active.is_(True))
        .filter(User.role.in_(('admin', 'hr')))
        .with_entities(User.id)
        .all()
    )
    return [r[0] for r in rows]


def notify_employees_dtr_review_open(year: int, month: int, quincena_half: str) -> None:
    label = quincena_period_label(year, month, quincena_half)
    link = url_for('routes.dtr_my_records', year=year, month=month, quincena=quincena_half)
    title = f'DTR available for review — {label}'
    message = (
        f'Biometric DTR for {label} has been uploaded. '
        'Review your record and submit justifications for any flagged days before the review deadline.'
    )
    for uid in _active_employee_user_ids():
        _notify_user(uid, title, message, link)


def notify_hr_justification_submitted(justification: DtrJustification) -> None:
    emp = justification.employee
    name = emp.full_name if emp else 'Employee'
    d = justification.record_date.strftime('%Y-%m-%d') if justification.record_date else ''
    link = url_for('routes.dtr_justifications')
    title = 'DTR justification submitted'
    message = f'{name} submitted a DTR justification for {d}.'
    for uid in _hr_admin_user_ids():
        _notify_user(uid, title, message, link)


def notify_employee_justification_decided(justification: DtrJustification, approved: bool) -> None:
    emp = justification.employee
    if not emp or not emp.user_id:
        return
    d = justification.record_date.strftime('%Y-%m-%d') if justification.record_date else ''
    link = url_for('routes.dtr_my_records')
    if approved:
        title = 'DTR justification approved'
        message = f'Your DTR justification for {d} was approved. HR may still adjust your DTR before worktime is processed.'
    else:
        title = 'DTR justification rejected'
        message = f'Your DTR justification for {d} was not approved. Contact HR if you have questions.'
    _notify_user(emp.user_id, title, message, link)


def notify_employees_worktime_processed(year: int, month: int, quincena_half: str) -> None:
    label = quincena_period_label(year, month, quincena_half)
    link = url_for('routes.dtr_my_records', year=year, month=month, quincena=quincena_half)
    title = f'Worktime processed — {label}'
    message = f'Final worktime for {label} has been processed. View your DTR record for details.'
    for uid in _active_employee_user_ids():
        _notify_user(uid, title, message, link)


def notify_hr_worktime_processed(year: int, month: int, quincena_half: str) -> None:
    label = quincena_period_label(year, month, quincena_half)
    link = url_for('routes.payroll_worktime_summary', year=year, month=month, quincena=quincena_half)
    title = f'Worktime ready for payroll — {label}'
    message = f'DTR worktime for {label} has been processed. You can proceed to Worktime Summary and payroll.'
    for uid in _hr_admin_user_ids():
        _notify_user(uid, title, message, link)
