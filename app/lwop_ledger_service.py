"""
Leave Without Pay (LWOP) ledger for plantilla employees.

- DTR quincena regen posts +1.000 / +1.250 lwop_days per unexcused absent weekday
  (5-day vs 4-day compressed workweek on that date).
- Approved DTR justifications reverse the same charged days for that record_date.
- Plantilla payroll 1st quincena deducts the full lwop_balance (monthly_rate / 22 per day).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func as sql_func

from app import db
from app.dtr_work_arrangement_service import arrangement_model_code_for_date
from app.leave_ledger_service import LEAVE_CREDITS_STATUSES, recompute_leave_ledger_balances
from app.leave_utils import leave_day_credit_for_workweek
from app.models import DtrJustification, Employee, LeaveLedger, SalaryGrade

LWOP_REGEN_PREFIX = 'LWOP_REGEN:'
LWOP_JUST_PREFIX = 'LWOP_JUST:'
LWOP_PAY_PREFIX = 'LWOP_PAY:'
LWOP_DAYS_PER_MONTH_DIVISOR = Decimal('22')


def _dec(val) -> Decimal:
    if val is None:
        return Decimal('0')
    return Decimal(str(val))


def lwop_day_charge_for_date(emp: Employee, work_date: date) -> Decimal:
    """Full-day LWOP charge for emp on work_date (1.000 or 1.250)."""
    return leave_day_credit_for_workweek(arrangement_model_code_for_date(emp, work_date))


def lwop_regen_remarks(work_date: date) -> str:
    return f'{LWOP_REGEN_PREFIX}{work_date.isoformat()}'


def lwop_just_remarks(justification_id: int) -> str:
    return f'{LWOP_JUST_PREFIX}{justification_id}'


def lwop_pay_remarks(year: int, month: int) -> str:
    return f'{LWOP_PAY_PREFIX}{year:04d}-{month:02d}-Q1'


def is_plantilla_lwop_employee(emp: Employee) -> bool:
    return (emp.status_of_appointment or '').strip() in LEAVE_CREDITS_STATUSES


def get_lwop_balance(employee_id: int) -> Decimal:
    row = (
        LeaveLedger.query.filter_by(employee_id=employee_id)
        .order_by(LeaveLedger.transaction_date.desc(), LeaveLedger.id.desc())
        .first()
    )
    if not row:
        return Decimal('0')
    return _dec(row.lwop_balance)


def _approved_justification_exists(employee_id: int, work_date: date) -> bool:
    return (
        DtrJustification.query.filter_by(
            employee_id=employee_id,
            record_date=work_date,
            status='approved',
        ).first()
        is not None
    )


def should_skip_lwop_accrual_for_day(employee_id: int, work_date: date) -> bool:
    """Skip LWOP when HR already approved a justification for that date."""
    return _approved_justification_exists(employee_id, work_date)


def delete_lwop_regen_rows_for_quincena(start_d: date, end_d: date) -> list[int]:
    """Remove per-day LWOP accrual rows in a quincena before re-posting."""
    rows = (
        LeaveLedger.query.filter(
            LeaveLedger.remarks.like(f'{LWOP_REGEN_PREFIX}%'),
            LeaveLedger.transaction_date >= start_d,
            LeaveLedger.transaction_date <= end_d,
        ).all()
    )
    affected = {r.employee_id for r in rows}
    for row in rows:
        db.session.delete(row)
    db.session.flush()
    for eid in affected:
        recompute_leave_ledger_balances(eid)
    return list(affected)


def post_lwop_for_absent_days(
    emp: Employee,
    day_rows: list[dict],
    *,
    created_by_user_id: int | None,
) -> bool:
    """Post +lwop_days per absent weekday using workweek day charge. Returns True if any row was added."""
    if not is_plantilla_lwop_employee(emp):
        return False
    added = False
    for day in day_rows:
        if day.get('absence_mins', 0) <= 0:
            continue
        work_date = day['work_date']
        if should_skip_lwop_accrual_for_day(emp.id, work_date):
            continue
        tag = lwop_regen_remarks(work_date)
        if LeaveLedger.query.filter_by(employee_id=emp.id, remarks=tag).first():
            continue
        day_charge = lwop_day_charge_for_date(emp, work_date)
        db.session.add(
            LeaveLedger(
                employee_id=emp.id,
                transaction_date=work_date,
                particulars=f'LWOP — absent on {work_date.strftime("%Y-%m-%d")}',
                lwop_days=day_charge,
                remarks=tag,
                created_by=created_by_user_id,
            )
        )
        added = True
    return added


def reverse_lwop_for_approved_justification(
    justification: DtrJustification,
    *,
    created_by_user_id: int | None,
) -> bool:
    """Reverse the LWOP day charge when HR approves a justification for a flagged absent day."""
    rev_tag = lwop_just_remarks(justification.id)
    if LeaveLedger.query.filter_by(employee_id=justification.employee_id, remarks=rev_tag).first():
        return False
    regen_tag = lwop_regen_remarks(justification.record_date)
    regen = LeaveLedger.query.filter_by(
        employee_id=justification.employee_id,
        remarks=regen_tag,
    ).first()
    if not regen:
        return False
    charged = _dec(regen.lwop_days)
    if charged <= 0:
        # Legacy / unexpected: fall back to current workweek charge for that date
        emp = Employee.query.get(justification.employee_id)
        charged = lwop_day_charge_for_date(emp, justification.record_date) if emp else Decimal('1')
    db.session.add(
        LeaveLedger(
            employee_id=justification.employee_id,
            transaction_date=justification.record_date,
            particulars=(
                f'LWOP reversal — justification approved '
                f'({justification.record_date.strftime("%Y-%m-%d")})'
            ),
            lwop_days=-charged,
            remarks=rev_tag,
            created_by=created_by_user_id,
        )
    )
    recompute_leave_ledger_balances(justification.employee_id)
    return True


def daily_lwop_rate(monthly_rate: Decimal) -> Decimal:
    if monthly_rate <= 0:
        return Decimal('0.00')
    return (monthly_rate / LWOP_DAYS_PER_MONTH_DIVISOR).quantize(Decimal('0.01'))


def _employee_monthly_rate(emp: Employee) -> Decimal:
    if not emp or not emp.salary_grade or not emp.salary_step or emp.salary_step < 1 or emp.salary_step > 8:
        return Decimal('0.00')
    row = SalaryGrade.query.filter(
        SalaryGrade.sg == emp.salary_grade,
        sql_func.coalesce(SalaryGrade.sg_agency, '') == (emp.agency or ''),
        sql_func.coalesce(SalaryGrade.sg_lgu_class, '') == (emp.lgu_class_level or ''),
        sql_func.coalesce(SalaryGrade.sg_tranche, '') == (emp.salary_tranche or ''),
    ).first()
    if not row:
        return Decimal('0.00')
    val = getattr(row, f'sg_step_{emp.salary_step}', None)
    if val is None:
        return Decimal('0.00')
    return Decimal(str(val)).quantize(Decimal('0.01'))


def _quincena_end_date(year: int, month: int, quincena: str) -> date:
    import calendar

    if quincena == '2':
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, last_day)
    return date(year, month, 15)


def preview_lwop_payroll_deduction(emp: Employee) -> tuple[Decimal, Decimal]:
    """
    Full LWOP balance deducted on the 1st quincena of each month.
    Returns (days, amount).
    """
    days = get_lwop_balance(emp.id)
    if days <= 0:
        return Decimal('0'), Decimal('0.00')
    monthly = _employee_monthly_rate(emp)
    amount = (days * daily_lwop_rate(monthly)).quantize(Decimal('0.01'))
    return days, amount


def any_lwop_payroll_deduction(employees: list[Employee]) -> bool:
    for emp in employees:
        days, _amt = preview_lwop_payroll_deduction(emp)
        if days > 0:
            return True
    return False


def post_lwop_payroll_settlement(
    emp: Employee,
    year: int,
    month: int,
    quincena: str,
    *,
    created_by_user_id: int | None,
) -> tuple[Decimal, Decimal]:
    """
    Settle full LWOP balance on 1st quincena payroll. Idempotent per period.
    Returns (days_settled, amount).
    """
    if quincena != '1':
        return Decimal('0'), Decimal('0.00')
    pay_tag = lwop_pay_remarks(year, month)
    if LeaveLedger.query.filter_by(employee_id=emp.id, remarks=pay_tag).first():
        row = LeaveLedger.query.filter_by(employee_id=emp.id, remarks=pay_tag).first()
        return abs(_dec(row.lwop_days)), _dec(row.lwop_amount)

    days, amount = preview_lwop_payroll_deduction(emp)
    if days <= 0:
        return Decimal('0'), Decimal('0.00')

    end_d = _quincena_end_date(year, month, quincena)
    db.session.add(
        LeaveLedger(
            employee_id=emp.id,
            transaction_date=end_d,
            particulars=(
                f'LWOP payroll settlement — {year:04d}-{month:02d} 1st quincena '
                f'({days} day(s), ₱{amount})'
            ),
            lwop_days=-days,
            lwop_amount=amount,
            remarks=pay_tag,
            created_by=created_by_user_id,
        )
    )
    recompute_leave_ledger_balances(emp.id)
    return days, amount
