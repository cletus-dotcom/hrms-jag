"""Plantilla monthly DTR submission (compliance track alongside quincena operations)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import g, url_for

from app import db
from app import dtr_quincena_period_service as dtr_period
from app.models import DtrMonthlyPeriod, DtrMonthlySubmission, Employee, HrmsNotification, User

STATUS_PENDING = 'pending'
STATUS_SUBMITTED = 'submitted'
STATUS_ACCEPTED = 'accepted'
STATUS_REJECTED = 'rejected'

STATUS_LABELS = {
    STATUS_PENDING: 'Not submitted',
    STATUS_SUBMITTED: 'Submitted — pending HR',
    STATUS_ACCEPTED: 'Accepted',
    STATUS_REJECTED: 'Rejected — resubmit required',
}

DEFAULT_SUBMISSION_DEADLINE_DAY = 5
REMINDER_DAYS_BEFORE = 3


def is_plantilla_employee(emp: Employee | None) -> bool:
    if not emp:
        return False
    text = ((emp.status_of_appointment or '') + ' ' + (emp.nature_of_appointment or '')).strip().lower()
    if ('job order' in text) or ('contract of service' in text) or ('cos' in text) or text == 'jo':
        return False
    soa = (emp.status_of_appointment or '').strip().lower()
    if soa:
        return ('permanent' in soa) or ('regular' in soa)
    return True


def _quincena_half_for_date(d: date) -> str:
    return '1' if d.day <= 15 else '2'


def _default_submission_deadline(year: int, month: int) -> date:
    if month == 12:
        return date(year + 1, 1, DEFAULT_SUBMISSION_DEADLINE_DAY)
    return date(year, month + 1, DEFAULT_SUBMISSION_DEADLINE_DAY)


def get_monthly_period(year: int, month: int) -> DtrMonthlyPeriod | None:
    return DtrMonthlyPeriod.query.filter_by(year=year, month=month).first()


def get_or_create_monthly_period(year: int, month: int) -> DtrMonthlyPeriod:
    row = get_monthly_period(year, month)
    if row:
        return row
    row = DtrMonthlyPeriod(
        year=year,
        month=month,
        submission_deadline=_default_submission_deadline(year, month),
    )
    db.session.add(row)
    db.session.flush()
    return row


def submission_deadline(year: int, month: int) -> date:
    mp = get_monthly_period(year, month)
    if mp and mp.submission_deadline:
        return mp.submission_deadline
    return _default_submission_deadline(year, month)


def _quincena_ready_for_monthly(period) -> bool:
    if period is None:
        return False
    return (period.status or '').strip() in (
        dtr_period.STATUS_REVIEW_CLOSED,
        dtr_period.STATUS_PROCESSED,
    )


def is_monthly_window_open(year: int, month: int) -> bool:
    q2 = dtr_period.get_period(year, month, '2')
    if not q2 or (q2.status or '').strip() == dtr_period.STATUS_DRAFT:
        return False
    if date.today() > submission_deadline(year, month):
        return False
    return True


def _submission_cache() -> dict:
    try:
        cache = getattr(g, '_dtr_monthly_submission_by_key', None)
    except RuntimeError:
        return {}
    if cache is None:
        cache = {}
        try:
            g._dtr_monthly_submission_by_key = cache
        except RuntimeError:
            pass
    return cache


def get_submission(emp: Employee, year: int, month: int) -> DtrMonthlySubmission | None:
    key = (emp.id, int(year), int(month))
    cache = _submission_cache()
    if key in cache:
        return cache[key]
    row = DtrMonthlySubmission.query.filter_by(
        employee_id=emp.id, year=year, month=month,
    ).first()
    cache[key] = row
    return row


def get_or_create_submission(emp: Employee, year: int, month: int) -> DtrMonthlySubmission:
    row = get_submission(emp, year, month)
    if row:
        return row
    row = DtrMonthlySubmission(employee_id=emp.id, year=year, month=month, status=STATUS_PENDING)
    db.session.add(row)
    db.session.flush()
    _submission_cache()[(emp.id, int(year), int(month))] = row
    return row


def monthly_period_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime('%B %Y')


def monthly_checklist(emp: Employee, year: int, month: int, rows: list) -> dict:
    """Build monthly submission readiness from pre-built DTR view rows."""
    flagged = [r for r in rows if r.get('is_flagged')]
    unjustified = [r for r in flagged if not r.get('has_justification')]
    q1 = dtr_period.get_period(year, month, '1')
    q2 = dtr_period.get_period(year, month, '2')
    sub = get_submission(emp, year, month)
    sub_status = (sub.status if sub else STATUS_PENDING) or STATUS_PENDING
    window_open = is_monthly_window_open(year, month)
    blockers: list[str] = []

    if not _quincena_ready_for_monthly(q1):
        blockers.append('1st quincena employee review is not closed yet.')
    if not _quincena_ready_for_monthly(q2):
        blockers.append('2nd quincena employee review is not closed yet.')
    if unjustified:
        blockers.append(f'{len(unjustified)} flagged day(s) still need justification.')
    if not window_open:
        if not q2 or (q2.status or '').strip() == dtr_period.STATUS_DRAFT:
            blockers.append('Monthly submission opens after the 2nd quincena DTR is uploaded.')
        elif date.today() > submission_deadline(year, month):
            blockers.append('Monthly submission deadline has passed. Contact HR.')
    if sub_status == STATUS_SUBMITTED:
        blockers.append('Monthly DTR already submitted — awaiting HR review.')
    if sub_status == STATUS_ACCEPTED:
        blockers.append('Monthly DTR already accepted by HR.')

    can_submit = (
        window_open
        and _quincena_ready_for_monthly(q1)
        and _quincena_ready_for_monthly(q2)
        and not unjustified
        and sub_status in (STATUS_PENDING, STATUS_REJECTED)
    )

    return {
        'flagged_count': len(flagged),
        'unjustified_count': len(unjustified),
        'unjustified_dates': [r['date'] for r in unjustified],
        'q1_status': dtr_period.STATUS_LABELS.get((q1.status if q1 else ''), 'Not started'),
        'q2_status': dtr_period.STATUS_LABELS.get((q2.status if q2 else ''), 'Not started'),
        'q1_ready': _quincena_ready_for_monthly(q1),
        'q2_ready': _quincena_ready_for_monthly(q2),
        'window_open': window_open,
        'deadline': submission_deadline(year, month),
        'submission': sub,
        'submission_status': sub_status,
        'submission_status_label': STATUS_LABELS.get(sub_status, sub_status),
        'can_submit': can_submit,
        'blockers': blockers,
    }


def can_plantilla_justify_date(emp: Employee, record_date: date) -> bool:
    """Plantilla: quincena review open OR monthly window with closed quincena for that day."""
    if not is_plantilla_employee(emp):
        return dtr_period.can_employee_submit_justification(
            dtr_period.get_period(record_date.year, record_date.month, _quincena_half_for_date(record_date))
        )
    sub = get_submission(emp, record_date.year, record_date.month)
    if sub and (sub.status or '').strip() == STATUS_ACCEPTED:
        return False
    half = _quincena_half_for_date(record_date)
    period = dtr_period.get_period(record_date.year, record_date.month, half)
    if dtr_period.can_employee_submit_justification(period):
        return True
    if not is_monthly_window_open(record_date.year, record_date.month):
        return False
    return _quincena_ready_for_monthly(period)


def can_justify_date_for_employee(emp: Employee, record_date: date) -> bool:
    if is_plantilla_employee(emp):
        return can_plantilla_justify_date(emp, record_date)
    half = _quincena_half_for_date(record_date)
    period = dtr_period.get_period(record_date.year, record_date.month, half)
    return dtr_period.can_employee_submit_justification(period)


def submit_monthly(emp: Employee, user_id: int | None, year: int, month: int, rows: list) -> tuple[bool, str]:
    checklist = monthly_checklist(emp, year, month, rows)
    if not checklist['can_submit']:
        msg = checklist['blockers'][0] if checklist['blockers'] else 'Monthly DTR cannot be submitted yet.'
        return False, msg
    row = get_or_create_submission(emp, year, month)
    now = datetime.utcnow()
    row.status = STATUS_SUBMITTED
    row.submitted_at = now
    row.submitted_by_user_id = user_id
    row.reviewed_at = None
    row.reviewed_by_user_id = None
    row.review_notes = None
    notify_hr_monthly_submitted(row)
    return True, 'Monthly DTR submitted successfully. HR will review your packet.'


def accept_submission(submission: DtrMonthlySubmission, user_id: int | None, notes: str = '') -> None:
    submission.status = STATUS_ACCEPTED
    submission.reviewed_at = datetime.utcnow()
    submission.reviewed_by_user_id = user_id
    submission.review_notes = (notes or '').strip() or None
    notify_employee_monthly_decided(submission, accepted=True)


def reject_submission(submission: DtrMonthlySubmission, user_id: int | None, notes: str = '') -> None:
    submission.status = STATUS_REJECTED
    submission.reviewed_at = datetime.utcnow()
    submission.reviewed_by_user_id = user_id
    submission.review_notes = (notes or '').strip() or None
    notify_employee_monthly_decided(submission, accepted=False)


def mark_printed(emp: Employee, year: int, month: int) -> None:
    row = get_or_create_submission(emp, year, month)
    row.printed_at = datetime.utcnow()


def plantilla_submissions_for_month(
    year: int,
    month: int,
    department_id: int | None = None,
) -> list[dict]:
    q = Employee.query.filter_by(status='active').order_by(Employee.last_name, Employee.first_name)
    if department_id:
        q = q.filter_by(department_id=department_id)
    employees = [e for e in q.all() if is_plantilla_employee(e)]
    subs = {
        (s.employee_id): s
        for s in DtrMonthlySubmission.query.filter_by(year=year, month=month).all()
    }
    deadline = submission_deadline(year, month)
    window_open = is_monthly_window_open(year, month)
    out = []
    for emp in employees:
        sub = subs.get(emp.id)
        status = (sub.status if sub else STATUS_PENDING) or STATUS_PENDING
        out.append({
            'employee': emp,
            'submission': sub,
            'status': status,
            'status_label': STATUS_LABELS.get(status, status),
            'submitted_at': sub.submitted_at if sub else None,
            'deadline': deadline,
            'window_open': window_open,
            'overdue': window_open and status in (STATUS_PENDING, STATUS_REJECTED) and date.today() > deadline,
        })
    return out


def _plantilla_user_ids() -> list[int]:
    rows = (
        db.session.query(User.id)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.status == 'active')
        .filter(User.is_active.is_(True))
        .all()
    )
    emp_by_uid = {e.user_id: e for e in Employee.query.filter_by(status='active').all() if e.user_id}
    return [uid for uid, in rows if is_plantilla_employee(emp_by_uid.get(uid))]


def _hr_admin_user_ids() -> list[int]:
    return [
        r[0] for r in User.query.filter(User.is_active.is_(True))
        .filter(User.role.in_(('admin', 'hr')))
        .with_entities(User.id).all()
    ]


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


def on_q2_uploaded(year: int, month: int) -> None:
    """Open monthly period and notify plantilla employees."""
    mp = get_or_create_monthly_period(year, month)
    if not mp.opened_at:
        mp.opened_at = datetime.utcnow()
    label = monthly_period_label(year, month)
    deadline = submission_deadline(year, month)
    link = url_for('routes.dtr_my_records', year=year, month=month, mode='month')
    title = f'Prepare monthly DTR — {label}'
    message = (
        f'The 2nd quincena DTR for {label} has been uploaded. '
        f'Review your full-month record, justify any flagged days, print your monthly DTR, '
        f'and submit before {deadline.strftime("%Y-%m-%d")}.'
    )
    for uid in _plantilla_user_ids():
        _notify_user(uid, title, message, link)


def maybe_notify_monthly_reminder(emp: Employee, year: int, month: int) -> None:
    """One reminder notification within REMINDER_DAYS_BEFORE of deadline if not yet submitted."""
    if not is_plantilla_employee(emp) or not is_monthly_window_open(year, month):
        return
    sub = get_submission(emp, year, month)
    if sub and (sub.status or '').strip() in (STATUS_SUBMITTED, STATUS_ACCEPTED):
        return
    dl = submission_deadline(year, month)
    days_left = (dl - date.today()).days
    if days_left < 0 or days_left > REMINDER_DAYS_BEFORE:
        return
    if not emp.user_id:
        return
    recent = (
        HrmsNotification.query.filter_by(user_id=emp.user_id)
        .filter(HrmsNotification.title.like('Monthly DTR reminder%'))
        .filter(HrmsNotification.created_at >= datetime.utcnow() - timedelta(days=2))
        .first()
    )
    if recent:
        return
    label = monthly_period_label(year, month)
    link = url_for('routes.dtr_my_records', year=year, month=month, mode='month')
    _notify_user(
        emp.user_id,
        f'Monthly DTR reminder — {label}',
        f'Your monthly DTR for {label} is due by {dl.strftime("%Y-%m-%d")} ({days_left} day(s) left).',
        link,
    )


def notify_hr_monthly_submitted(submission: DtrMonthlySubmission) -> None:
    emp = submission.employee
    label = monthly_period_label(submission.year, submission.month)
    name = emp.full_name if emp else 'Employee'
    link = url_for('routes.dtr_monthly_submissions', year=submission.year, month=submission.month)
    for uid in _hr_admin_user_ids():
        _notify_user(uid, f'Monthly DTR submitted — {label}', f'{name} submitted monthly DTR for {label}.', link)


def notify_employee_monthly_decided(submission: DtrMonthlySubmission, accepted: bool) -> None:
    emp = submission.employee
    if not emp or not emp.user_id:
        return
    label = monthly_period_label(submission.year, submission.month)
    link = url_for('routes.dtr_my_records', year=submission.year, month=submission.month, mode='month')
    if accepted:
        title = f'Monthly DTR accepted — {label}'
        message = f'HR accepted your monthly DTR submission for {label}.'
    else:
        title = f'Monthly DTR returned — {label}'
        message = f'HR returned your monthly DTR for {label}. Review notes and resubmit.'
    _notify_user(emp.user_id, title, message, link)
