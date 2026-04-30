"""
Leave ledger balance recomputation and scheduled monthly leave accrual.

VL/SL: 1.25 days per month; new appointees in a partial first calendar month use
the daily (TABLE 2) points from appointment date through month-end (hire after the 1st).

SPL/WL: 3 SPL and 5 WL on appointment (ledger row dated appointment date).
        3 SPL and 5 WL each January 1 for anyone appointed before that calendar year’s Jan 1
        (Jan 1 appointees rely on the appointment grant only).
        On Dec 31, unused SPL and WL balances are posted as spl_used / wl_used (year-end lapse).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal

from app import db
from app.leave_utils import (
    MONTHLY_SL_EARNED,
    MONTHLY_VL_EARNED,
    YEARLY_SPL_EARNED,
    YEARLY_WL_EARNED,
    get_daily_leave_earned,
    count_working_weekdays,
)
from app.models import Employee, LeaveLedger, LeaveLedgerDeletion

# Same eligibility as leave_credits_list
LEAVE_CREDITS_STATUSES = ('Permanent', 'Casual', 'Temporary', 'Elective')


def monthly_accrual_particulars(year: int, month: int) -> str:
    # Stable string for idempotency (do not change format for existing deployments).
    return f'Monthly VL/SL accrual - {year:04d}-{month:02d}'


def _eligible_leave_credits(emp: Employee) -> bool:
    return (emp.status_of_appointment or '').strip() in LEAVE_CREDITS_STATUSES


def _dec(val) -> Decimal:
    if val is None:
        return Decimal('0')
    return Decimal(str(val))


def appointment_spl_wl_particulars(appt: date) -> str:
    return f'SPL/WL grant on appointment - {appt.isoformat()}'


def annual_spl_wl_particulars(year: int) -> str:
    return f'Annual SPL/WL grant - {year:04d}'


def year_end_lapse_particulars(year: int) -> str:
    return f'SPL/WL year-end lapse - {year:04d}'


def recompute_leave_ledger_balances(employee_id: int) -> None:
    """Recalculate running balances for one employee from ledger rows (ordered by date, id)."""
    entries = (
        LeaveLedger.query.filter_by(employee_id=employee_id)
        .order_by(LeaveLedger.transaction_date, LeaveLedger.id)
        .all()
    )

    prev = {
        'vl_balance': Decimal('0'),
        'sl_balance': Decimal('0'),
        'spl_balance': Decimal('0'),
        'wl_balance': Decimal('0'),
        'ml_balance': Decimal('0'),
        'pl_balance': Decimal('0'),
        'sp_balance': Decimal('0'),
        'avaw_balance': Decimal('0'),
        'study_balance': Decimal('0'),
        'rehab_balance': Decimal('0'),
        'slbw_balance': Decimal('0'),
        'se_calamity_balance': Decimal('0'),
        'adopt_balance': Decimal('0'),
        'cto_balance': Decimal('0'),
    }

    for e in entries:
        e.vl_balance = (
            prev['vl_balance']
            + _dec(e.vl_earned)
            - _dec(e.vl_applied)
            - _dec(e.vl_tardiness)
            - _dec(e.vl_undertime)
        )
        e.sl_balance = prev['sl_balance'] + _dec(e.sl_earned) - _dec(e.sl_applied)
        e.spl_balance = prev['spl_balance'] + _dec(e.spl_earned) - _dec(e.spl_used)
        e.wl_balance = prev['wl_balance'] + _dec(e.wl_earned) - _dec(e.wl_used)
        e.ml_balance = prev['ml_balance'] + _dec(e.ml_credits) - _dec(e.ml_used)
        e.pl_balance = prev['pl_balance'] + _dec(e.pl_credits) - _dec(e.pl_used)
        e.sp_balance = prev['sp_balance'] + _dec(e.sp_credits) - _dec(e.sp_used)
        e.avaw_balance = prev['avaw_balance'] + _dec(e.avaw_credits) - _dec(e.avaw_used)
        e.study_balance = prev['study_balance'] + _dec(e.study_credits) - _dec(e.study_used)
        e.rehab_balance = prev['rehab_balance'] + _dec(e.rehab_credits) - _dec(e.rehab_used)
        e.slbw_balance = prev['slbw_balance'] + _dec(e.slbw_credits) - _dec(e.slbw_used)
        e.se_calamity_balance = (
            prev['se_calamity_balance']
            + _dec(e.se_calamity_credits)
            - _dec(e.se_calamity_used)
        )
        e.adopt_balance = prev['adopt_balance'] + _dec(e.adopt_credits) - _dec(e.adopt_used)
        e.cto_balance = prev['cto_balance'] + _dec(e.cto_earned) - _dec(e.cto_used)

        prev['vl_balance'] = _dec(e.vl_balance)
        prev['sl_balance'] = _dec(e.sl_balance)
        prev['spl_balance'] = _dec(e.spl_balance)
        prev['wl_balance'] = _dec(e.wl_balance)
        prev['ml_balance'] = _dec(e.ml_balance)
        prev['pl_balance'] = _dec(e.pl_balance)
        prev['sp_balance'] = _dec(e.sp_balance)
        prev['avaw_balance'] = _dec(e.avaw_balance)
        prev['study_balance'] = _dec(e.study_balance)
        prev['rehab_balance'] = _dec(e.rehab_balance)
        prev['slbw_balance'] = _dec(e.slbw_balance)
        prev['se_calamity_balance'] = _dec(e.se_calamity_balance)
        prev['adopt_balance'] = _dec(e.adopt_balance)
        prev['cto_balance'] = _dec(e.cto_balance)


def _vl_sl_for_month(emp: Employee, year: int, month: int) -> tuple[Decimal, Decimal]:
    """
    VL and SL for the accrual month: 1.25 each.

    New appointee in a *partial* first calendar month: TABLE 2 from appointment through
    month-end when hired after the 1st. Appointment on the 1st: full 1.25 each.
    """
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    first_day = date(year, month, 1)
    appt = emp.appointment_date

    if appt and appt > last_day:
        return Decimal('0'), Decimal('0')
    if appt and first_day < appt <= last_day:
        wd = count_working_weekdays(appt, last_day)
        earned = get_daily_leave_earned(min(wd, 30))
        return earned, earned
    return MONTHLY_VL_EARNED, MONTHLY_SL_EARNED


def _latest_spl_wl_balances(employee_id: int) -> tuple[Decimal, Decimal]:
    recompute_leave_ledger_balances(employee_id)
    last = (
        LeaveLedger.query.filter_by(employee_id=employee_id)
        .order_by(LeaveLedger.transaction_date.desc(), LeaveLedger.id.desc())
        .first()
    )
    if not last:
        return Decimal('0'), Decimal('0')
    return _dec(last.spl_balance), _dec(last.wl_balance)


def ensure_appointment_spl_wl_grant(
    employee_id: int,
    *,
    created_by_user_id: int | None = None,
) -> bool:
    """
    Post 3 SPL + 5 WL on the employee's appointment date if leave-credits eligible.
    Idempotent per appointment date. Returns True if a new row was added.
    """
    emp = Employee.query.get(employee_id)
    if not emp or not _eligible_leave_credits(emp):
        return False
    if (emp.status or '').strip().lower() == 'terminated':
        return False
    appt = emp.appointment_date
    if not appt:
        return False

    particulars = appointment_spl_wl_particulars(appt)
    if LeaveLedger.query.filter_by(employee_id=emp.id, particulars=particulars).first():
        return False

    row = LeaveLedger(
        employee_id=emp.id,
        transaction_date=appt,
        particulars=particulars,
        remarks='SPL and WL on appointment',
        spl_earned=YEARLY_SPL_EARNED,
        wl_earned=YEARLY_WL_EARNED,
        created_by=created_by_user_id,
    )
    db.session.add(row)
    db.session.flush()
    recompute_leave_ledger_balances(emp.id)
    return True


def accrue_monthly_vl_sl_for_month(
    year: int,
    month: int,
    *,
    created_by_user_id: int | None = None,
) -> dict:
    """
    VL/SL: last day of month.

    January: also posts Jan 1 annual SPL/WL (3 + 5) for employees appointed before that
    Jan 1 (appointment grant covers Jan 1 hires).

    December: after VL/SL, posts Dec 31 lapse of remaining SPL/WL balances.

    Returns counts for UI / logging.
    """
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    particulars = monthly_accrual_particulars(year, month)

    employees = (
        Employee.query.filter(Employee.status_of_appointment.in_(LEAVE_CREDITS_STATUSES))
        .order_by(Employee.id)
        .all()
    )

    touched_ids: set[int] = set()
    created_vlsl = 0
    skipped_duplicate = 0
    skipped_not_employed = 0
    skipped_terminated = 0

    # --- January: annual SPL/WL first (dated Jan 1) so ordering is correct vs Jan 31 VL/SL ---
    created_annual = 0
    skipped_annual_duplicate = 0
    if month == 1:
        jan1 = date(year, 1, 1)
        annual_p = annual_spl_wl_particulars(year)
        for emp in employees:
            if (emp.status or '').strip().lower() == 'terminated':
                continue
            appt = emp.appointment_date
            if appt is not None and appt >= jan1:
                continue
            existing = LeaveLedger.query.filter_by(
                employee_id=emp.id,
                transaction_date=jan1,
                particulars=annual_p,
            ).first()
            if existing:
                skipped_annual_duplicate += 1
                continue
            db.session.add(
                LeaveLedger(
                    employee_id=emp.id,
                    transaction_date=jan1,
                    particulars=annual_p,
                    remarks='Annual SPL and WL (start of year)',
                    spl_earned=YEARLY_SPL_EARNED,
                    wl_earned=YEARLY_WL_EARNED,
                    created_by=created_by_user_id,
                )
            )
            touched_ids.add(emp.id)
            created_annual += 1

    # --- Monthly VL/SL (last day of month) ---
    for emp in employees:
        if (emp.status or '').strip().lower() == 'terminated':
            skipped_terminated += 1
            continue

        vl_amt, sl_amt = _vl_sl_for_month(emp, year, month)
        if vl_amt == 0 and sl_amt == 0:
            skipped_not_employed += 1
            continue

        existing = LeaveLedger.query.filter_by(
            employee_id=emp.id,
            transaction_date=last_day,
            particulars=particulars,
        ).first()
        if existing:
            skipped_duplicate += 1
            continue

        db.session.add(
            LeaveLedger(
                employee_id=emp.id,
                transaction_date=last_day,
                particulars=particulars,
                remarks='System monthly accrual',
                vl_earned=vl_amt,
                sl_earned=sl_amt,
                created_by=created_by_user_id,
            )
        )
        touched_ids.add(emp.id)
        created_vlsl += 1

    db.session.flush()
    for eid in touched_ids:
        recompute_leave_ledger_balances(eid)

    # --- December: year-end lapse of unused SPL/WL on Dec 31 ---
    created_lapse = 0
    skipped_lapse_duplicate = 0
    skipped_lapse_no_balance = 0
    if month == 12:
        dec31 = date(year, 12, 31)
        lapse_p = year_end_lapse_particulars(year)
        lapse_emp_ids: list[int] = []
        for emp in employees:
            if (emp.status or '').strip().lower() == 'terminated':
                continue
            appt = emp.appointment_date
            if appt and appt > dec31:
                continue
            if LeaveLedger.query.filter_by(
                employee_id=emp.id,
                transaction_date=dec31,
                particulars=lapse_p,
            ).first():
                skipped_lapse_duplicate += 1
                continue

            spl_bal, wl_bal = _latest_spl_wl_balances(emp.id)
            spl_use = max(Decimal('0'), spl_bal)
            wl_use = max(Decimal('0'), wl_bal)
            if spl_use == 0 and wl_use == 0:
                skipped_lapse_no_balance += 1
                continue

            db.session.add(
                LeaveLedger(
                    employee_id=emp.id,
                    transaction_date=dec31,
                    particulars=lapse_p,
                    remarks='Year-end forfeit of unused SPL/WL',
                    spl_used=spl_use,
                    wl_used=wl_use,
                    created_by=created_by_user_id,
                )
            )
            lapse_emp_ids.append(emp.id)
            created_lapse += 1
        db.session.flush()
        for eid in lapse_emp_ids:
            recompute_leave_ledger_balances(eid)

    db.session.commit()

    return {
        'year': year,
        'month': month,
        'transaction_date': last_day.isoformat(),
        'created': created_vlsl,
        'created_annual_spl_wl': created_annual,
        'skipped_annual_duplicate': skipped_annual_duplicate,
        'created_year_end_lapse': created_lapse,
        'skipped_lapse_duplicate': skipped_lapse_duplicate,
        'skipped_lapse_no_balance': skipped_lapse_no_balance,
        'skipped_duplicate': skipped_duplicate,
        'skipped_not_employed': skipped_not_employed,
        'skipped_terminated': skipped_terminated,
    }


def record_leave_ledger_deletion(
    entry: LeaveLedger,
    *,
    deleted_by_user_id: int | None,
    deleted_by_username: str | None,
    source: str,
) -> None:
    """Persist a full snapshot of a ledger row before it is removed from leave_ledger."""
    uname = (deleted_by_username or '').strip()[:80] or None
    src = (source or 'unknown').strip()[:40] or 'unknown'
    snap = LeaveLedgerDeletion(
        original_ledger_id=entry.id,
        employee_id=entry.employee_id,
        deleted_at=datetime.utcnow(),
        deleted_by_user_id=deleted_by_user_id,
        deleted_by_username=uname,
        delete_source=src,
        transaction_date=entry.transaction_date,
        particulars=entry.particulars,
        remarks=entry.remarks,
        orig_created_by=entry.created_by,
        orig_created_at=entry.created_at,
        vl_earned=entry.vl_earned,
        vl_applied=entry.vl_applied,
        vl_tardiness=entry.vl_tardiness,
        vl_undertime=entry.vl_undertime,
        vl_balance=entry.vl_balance,
        sl_earned=entry.sl_earned,
        sl_applied=entry.sl_applied,
        sl_balance=entry.sl_balance,
        spl_earned=entry.spl_earned,
        spl_used=entry.spl_used,
        spl_balance=entry.spl_balance,
        wl_earned=entry.wl_earned,
        wl_used=entry.wl_used,
        wl_balance=entry.wl_balance,
        ml_credits=entry.ml_credits,
        ml_used=entry.ml_used,
        ml_balance=entry.ml_balance,
        pl_credits=entry.pl_credits,
        pl_used=entry.pl_used,
        pl_balance=entry.pl_balance,
        sp_credits=entry.sp_credits,
        sp_used=entry.sp_used,
        sp_balance=entry.sp_balance,
        avaw_credits=entry.avaw_credits,
        avaw_used=entry.avaw_used,
        avaw_balance=entry.avaw_balance,
        study_credits=entry.study_credits,
        study_used=entry.study_used,
        study_balance=entry.study_balance,
        rehab_credits=entry.rehab_credits,
        rehab_used=entry.rehab_used,
        rehab_balance=entry.rehab_balance,
        slbw_credits=entry.slbw_credits,
        slbw_used=entry.slbw_used,
        slbw_balance=entry.slbw_balance,
        se_calamity_credits=entry.se_calamity_credits,
        se_calamity_used=entry.se_calamity_used,
        se_calamity_balance=entry.se_calamity_balance,
        adopt_credits=entry.adopt_credits,
        adopt_used=entry.adopt_used,
        adopt_balance=entry.adopt_balance,
        cto_earned=entry.cto_earned,
        cto_used=entry.cto_used,
        cto_balance=entry.cto_balance,
    )
    db.session.add(snap)
