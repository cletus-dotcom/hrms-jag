from flask import Blueprint, render_template, request, redirect, url_for, flash, session, Response, jsonify, send_file, abort, send_from_directory, g
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, date, time, timedelta
from types import SimpleNamespace
import calendar
import json
import re
import csv
import io
import os
from app import db
from app.config import (
    DTR_JUSTIFICATION_REASON_OTHERS,
    DTR_JUSTIFICATION_REASONS,
    EMPLOYEE_SEPARATION_TYPES,
    EMPLOYEE_STATUSES,
)
from app.leave_ledger_service import (
    LEAVE_CREDITS_STATUSES,
    accrue_monthly_vl_sl_for_month,
    ensure_appointment_spl_wl_grant,
    record_leave_ledger_deletion,
    recompute_leave_ledger_balances,
    undo_monthly_accrual_for_month,
)
from app.leave_utils import minutes_to_day_equivalent, leave_day_credit_for_workweek
from app.workweek_calendar_service import (
    build_year_view,
    calendar_model_code_for_date,
    save_year_calendar,
)
from app.lwop_ledger_service import (
    any_lwop_payroll_deduction,
    delete_lwop_regen_rows_for_quincena,
    post_lwop_for_absent_days,
    post_lwop_payroll_settlement,
    preview_lwop_payroll_deduction,
    reverse_lwop_for_approved_justification,
)
from app.models import User, Employee, Department, Position, SalaryGrade, Attendance, LeaveRequest, LeaveType, LeaveBalance, EmployeePDS, EmployeeAppointmentHistory, DailyTimeRecord, DtrUploadBatch, DtrUploadBatchItem, DtrUploadPunch, DtrUploadPunchFlag, DtrQuincenaPeriod, DtrMonthlySubmission, LeaveLedger, DtrWorkArrangementSetting, DtrJustification, DtrAccomplishmentReport, JoCosDesignation, JoCosRate, FlexiTimeSchedule, EmployeeFlexiDay, DtrQuincenaWorktimeSummary, WorkHours, FlexibleWorktime, OvertimeAuthorization, JoCosExtendService, JoCosOvertime, JoCosOvertimeLedger, JoCosOvertimeOffsetRequest, HrmsNotification, GsisContribution, GsisLoanRecord, GsisLoanDeduction, PhicContribution, LbpBillingRecord, LbpBillingDeduction, JofemcoBillingRecord, JofemcoBillingDeduction, WithholdingTaxRecord, WithholdingTaxDeduction, HdmfContributionRecord, HdmfContributionDeduction, HdmfLoanRecord, HdmfLoanDeduction, JoCosPayslip, PlantillaPayslip, EventAttendance, EventAttendanceBatch, EVENT_ATTENDANCE_CATEGORIES, EVENT_ATTENDANCE_CATEGORY_LABELS
from app.dtr_parse import parse_dtr_dat_file as _parse_dtr_dat_file
from app.dtr_upload_staging import discard_staged_dtr_upload, load_staged_dtr_upload_rows
from app.dtr_upload_batch_service import (
    DTR_UPLOAD_QUINCENA_BUFFER_DAYS,
    SLOT_LABELS as DTR_PUNCH_SLOT_LABELS,
    apply_day_punch_assignments,
    apply_selected_punch_to_dtr,
    can_undo_dtr_batch,
    count_open_punch_flags,
    create_upload_batch,
    list_punch_flags_for_batch,
    list_recent_dtr_upload_batches,
    log_batch_item,
    open_punch_flags_by_date,
    persist_upload_punches_and_flags,
    punches_by_employee_day_for_batch,
    punches_for_employee_day,
    punches_for_flag,
    quincena_core_date_range,
    quincena_upload_accept_range,
    reviewable_punch_flags_by_date,
    snapshot_dtr_row,
    undo_dtr_upload_batch,
)
from app import dtr_quincena_period_service as dtr_period
from app import dtr_work_arrangement_service as dtr_wa
from app.dtr_jo_cos_actual_punch_service import (
    merge_prefer_actual_over_duty_stamp,
    restore_actual_dtr_punches,
)
from app import dtr_monthly_submission_service as dtr_monthly
from app.dtr_accomplishment_service import (
    approved_accomplishment_export_rows,
    enrich_jo_cos_dtr_rows,
    has_approved_justification_for_export,
    save_accomplishment_reports,
)
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from werkzeug.utils import secure_filename
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

bp = Blueprint('routes', __name__)


@bp.app_context_processor
def inject_hrms_globals():
    from flask_login import current_user
    from app.portal_auth import is_portal_admin_session

    branding_ctx = {}
    try:
        from app.portal_site_service import get_branding, branding_logo_url, branding_seal_url
        branding = get_branding()
        branding_ctx = {
            'site_branding': branding,
            'site_logo_url': branding_logo_url(branding),
            'site_seal_url': branding_seal_url(branding),
            'is_portal_admin_session': is_portal_admin_session(),
        }
    except Exception:
        branding_ctx = {
            'site_branding': {},
            'site_logo_url': url_for('static', filename='hrmdo_logo_transparent.png'),
            'site_seal_url': url_for('static', filename='jagna_logo.png'),
            'is_portal_admin_session': is_portal_admin_session(),
        }

    if not current_user.is_authenticated:
        return {'unread_notification_count': 0, 'employee': None, **branding_ctx}
    try:
        uid = getattr(current_user, 'id', None)
        if uid:
            count = HrmsNotification.query.filter_by(
                user_id=uid, is_read=False
            ).count()
        else:
            count = 0
    except Exception:
        count = 0
    emp = _current_employee_for_user() if current_user.is_authenticated else None
    return {'unread_notification_count': count, 'employee': emp, **branding_ctx}

# Employees list report — matches Add/Edit Employee "Status of appointment" options
REPORT_EMPLOYEE_PLANTILLA_STATUSES = (
    'Permanent', 'Casual', 'Contractual', 'Temporary', 'Coterminus',
    'Probational', 'Provisional', 'Elective',
)
REPORT_EMPLOYEE_NON_PLANTILLA_STATUSES = ('Job Order', 'Contract of Service')


def _jo_cos_designations_ordered():
    return JoCosDesignation.query.order_by(JoCosDesignation.sort_order, JoCosDesignation.id).all()


def _normalize_jo_cos_designation_form(text):
    """Collapse whitespace (same idea as Excel import) for consistent UNIQUE checks."""
    if text is None:
        return None
    s = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s or s.lower() == "nan":
        return None
    return " ".join(s.split())


def _next_jo_cos_designation_sort_order():
    v = db.session.query(func.max(JoCosDesignation.sort_order)).scalar()
    return int(v or 0) + 1


def _is_jo_or_cos_status(status):
    s = (status or '').strip().lower()
    return s in ('job order', 'contract of service')


def _resolve_position_and_jo_cos_fk(status_of_appointment, position_form_val, jo_cos_id_form_val):
    """
    For Job Order / Contract of Service, current position comes from jo_cos_designation_id when set.
    Otherwise use the plantilla/positions dropdown value.
    """
    pos_in = (position_form_val or '').strip()
    jraw = (jo_cos_id_form_val or '').strip()
    if _is_jo_or_cos_status(status_of_appointment):
        jid = None
        if jraw:
            try:
                jid = int(jraw)
            except ValueError:
                jid = None
        row = JoCosDesignation.query.get(jid) if jid else None
        if row:
            des = (row.designation or '').strip()
            return (des if des else None), row.id
        return (pos_in or None), None
    return (pos_in or None), None

def _next_employee_id_5digit() -> str:
    """
    Returns the next 5-digit numeric employee_id, based on the current maximum
    1–5 digit numeric employee_id in the database.
    """
    from sqlalchemy import text

    # Postgres regex (~) matches up to 5-digit numeric IDs (e.g. '40555').
    max_val = db.session.execute(
        text(
            r"SELECT MAX(CAST(employee_id AS INTEGER)) "
            r"FROM employees "
            r"WHERE employee_id ~ '^[0-9]{1,5}$'"
        )
    ).scalar()
    next_val = (int(max_val) if max_val is not None else 0) + 1
    # Keep 5-digit padding; if it overflows 99999, return the full number.
    return f"{next_val:05d}" if next_val <= 99999 else str(next_val)


def _validate_employee_badge_id(employee_id: str) -> str:
    """Require exactly 5 numeric digits for new/changed employee badge IDs."""
    badge = (employee_id or '').strip()
    if not re.fullmatch(r'[0-9]{5}', badge):
        raise ValueError('Employee ID must be exactly 5 digits.')
    return badge



def _get_leave_balances_for_employee(emp: Employee) -> dict:
    """Get latest balances from leave_ledger for an employee."""
    last = (LeaveLedger.query
            .filter_by(employee_id=emp.id)
            .order_by(LeaveLedger.transaction_date.desc(), LeaveLedger.id.desc())
            .first())
    if not last:
        return {
            'vl': Decimal('0'), 'sl': Decimal('0'),
            'spl': Decimal('0'), 'wl': Decimal('0'), 'cto': Decimal('0'),
            'ml': Decimal('0'), 'pl': Decimal('0'), 'sp': Decimal('0'), 'avaw': Decimal('0'),
            'study': Decimal('0'), 'rehab': Decimal('0'), 'slbw': Decimal('0'),
            'se_calamity': Decimal('0'), 'adopt': Decimal('0'),
        }
    def d(v):
        return Decimal(str(v)) if v is not None else Decimal('0')
    return {
        'vl': d(last.vl_balance),
        'sl': d(last.sl_balance),
        'spl': d(last.spl_balance),
        'wl': d(last.wl_balance),
        'cto': d(last.cto_balance),
        'ml': d(last.ml_balance),
        'pl': d(last.pl_balance),
        'sp': d(last.sp_balance),
        'avaw': d(last.avaw_balance),
        'study': d(last.study_balance),
        'rehab': d(last.rehab_balance),
        'slbw': d(last.slbw_balance),
        'se_calamity': d(last.se_calamity_balance),
        'adopt': d(last.adopt_balance),
    }


def _apply_leave_hidden_codes(emp: Employee) -> frozenset[str]:
    """
    Leave types to hide on Apply Leave credits panel from PDS sex / civil status.
    - Male, not married: ML, PL, VAWC, SLBW
    - Male, married: ML, SP, VAWC, SLBW
    - Female, not married: PL
    - Female, married: PL, SP
    If sex/civil unknown, nothing is hidden by this rule.
    """
    pds = EmployeePDS.query.filter_by(employee_id=emp.id).first()
    sex = ((pds.sex_at_birth if pds else '') or '').strip().lower()
    civil = ((pds.civil_status if pds else '') or '').strip().lower()
    is_married = civil == 'married'
    is_female = bool(sex) and sex.startswith('f')
    is_male = bool(sex) and not is_female

    if is_male and not is_married:
        return frozenset({'ML', 'PL', 'VAWC', 'SLBW'})
    if is_male and is_married:
        return frozenset({'ML', 'SP', 'VAWC', 'SLBW'})
    if is_female and not is_married:
        return frozenset({'PL'})
    if is_female and is_married:
        return frozenset({'PL', 'SP'})
    return frozenset()


def _leave_apply_credit_groups(emp: Employee, balances: dict) -> list[dict]:
    """
    Credits panel: same leave families as leave_ledger, only types with balance > 0,
    and sex/civil rules from _apply_leave_hidden_codes.
    """
    hidden = _apply_leave_hidden_codes(emp)

    def bal(key: str) -> Decimal:
        v = balances.get(key)
        if v is None:
            return Decimal('0')
        return Decimal(str(v))

    def row(code: str, label: str, key: str) -> dict:
        return {'code': code, 'label': label, 'balance': bal(key)}

    def include_row(r: dict) -> bool:
        code = (r.get('code') or '').strip()
        if code in hidden:
            return False
        return r['balance'] > Decimal('0')

    vl, sl = bal('vl'), bal('sl')
    vac_rows = []
    if vl > Decimal('0'):
        vac_rows.append(row('VL', 'Vacation Leave', 'vl'))
    if sl > Decimal('0'):
        vac_rows.append(row('SL', 'Sick Leave', 'sl'))
    if vac_rows and (vl + sl) > Decimal('0'):
        vac_rows.append({
            'code': '—',
            'label': 'Total VL + SL',
            'balance': vl + sl,
            'subtotal': True,
        })

    def filter_group(rows: list[dict]) -> list[dict]:
        return [r for r in rows if include_row(r)]

    spwl_raw = [
        row('SPL', 'Special Privilege Leave', 'spl'),
        row('WL', 'Wellness Leave', 'wl'),
    ]
    other_raw = [
        row('ML', 'Maternity Leave', 'ml'),
        row('PL', 'Paternity Leave', 'pl'),
        row('SP', 'Solo Parent Leave', 'sp'),
        row('VAWC', 'Anti-Violence Against Women (AVAW)', 'avaw'),
        row('STUDY', 'Study Leave', 'study'),
        row('REHAB', 'Rehabilitation Leave', 'rehab'),
        row('SLBW', 'Special Leave Benefits for Women (SLBW)', 'slbw'),
        row('SE_CALAMITY', 'Special Emergency (Calamity)', 'se_calamity'),
        row('ADOPT', 'Adoption Leave', 'adopt'),
    ]
    cto_raw = [row('CTO', 'Credit Time Off (CTO)', 'cto')]

    groups = []
    if vac_rows:
        groups.append({'title': 'Vacation & sick', 'rows': vac_rows})
    spwl = filter_group(spwl_raw)
    if spwl:
        groups.append({'title': 'Special privilege & wellness', 'rows': spwl})
    other = filter_group(other_raw)
    if other:
        groups.append({'title': 'Other kinds of leave', 'rows': other})
    cto = filter_group(cto_raw)
    if cto:
        groups.append({'title': 'Compensatory time off', 'rows': cto})

    return groups


LEAVE_TYPE_OPTIONS = [
    ('VL', 'Vacation Leave'),
    ('MFL', 'Mandatory/Forced Leave'),
    ('SL', 'Sick Leave'),
    ('ML', 'Maternity Leave'),
    ('PL', 'Paternity Leave'),
    ('SPL', 'Special Privilege Leave'),
    ('SP', 'Solo Parent Leave'),
    ('STUDY', 'Study Leave'),
    ('VAWC', '10-day VAWC Leave'),
    ('REHAB', 'Rehabilitation Privilege'),
    ('SLBW', 'Special Leave Benefits for Women'),
    ('SE_CALAMITY', 'Special Emergency (Calamity) Leave'),
    ('ADOPT', 'Adoption Leave'),
    ('CTO', 'Compensatory Time Off'),
    ('WL', 'Wellness Leave'),
]


def _leave_code_to_balance_key(code: str) -> str:
    c = (code or '').strip().upper()
    if c in ('VL', 'MFL'):
        return 'vl'
    if c == 'SL':
        return 'sl'
    if c == 'SPL':
        return 'spl'
    if c == 'WL':
        return 'wl'
    if c == 'CTO':
        return 'cto'
    if c == 'ML':
        return 'ml'
    if c == 'PL':
        return 'pl'
    if c == 'SP':
        return 'sp'
    if c in ('VAWC',):
        return 'avaw'
    if c == 'STUDY':
        return 'study'
    if c == 'REHAB':
        return 'rehab'
    if c == 'SLBW':
        return 'slbw'
    if c == 'SE_CALAMITY':
        return 'se_calamity'
    if c == 'ADOPT':
        return 'adopt'
    return ''


@bp.route('/favicon.ico')
def favicon():
    """Avoid 404 when browser requests favicon."""
    return Response(status=204)


def _serialize_salary_grades(salary_grades):
    """Convert SalaryGrade list to JSON-serializable list of dicts for the employee form."""
    out = []
    for s in salary_grades:
        out.append({
            'sg': s.sg,
            'sg_agency': (s.sg_agency or '').strip(),
            'sg_tranche': (s.sg_tranche or '').strip(),
            'sg_lgu_class': (s.sg_lgu_class or '').strip(),
            'sg_step_1': float(s.sg_step_1) if s.sg_step_1 is not None else None,
            'sg_step_2': float(s.sg_step_2) if s.sg_step_2 is not None else None,
            'sg_step_3': float(s.sg_step_3) if s.sg_step_3 is not None else None,
            'sg_step_4': float(s.sg_step_4) if s.sg_step_4 is not None else None,
            'sg_step_5': float(s.sg_step_5) if s.sg_step_5 is not None else None,
            'sg_step_6': float(s.sg_step_6) if s.sg_step_6 is not None else None,
            'sg_step_7': float(s.sg_step_7) if s.sg_step_7 is not None else None,
            'sg_step_8': float(s.sg_step_8) if s.sg_step_8 is not None else None,
        })
    return out


def _agency_from_department(dept):
    """Map department to salary schedule agency. MHO/RHU use National; others Local."""
    if not dept:
        return None
    name = (getattr(dept, 'name', None) or '').strip().upper()
    if name in ('MHO', 'RHU'):
        return 'National'
    return 'Local'

def _dashboard_for_user(user):
    """Return dashboard route name for post-login redirect: employee role -> employee_dashboard, else dashboard."""
    if not user:
        return 'routes.dashboard'
    role = (getattr(user, 'role', None) or '').strip().lower()
    if role in ('employee', 'dtr_uploader'):
        return 'routes.employee_dashboard'
    # If user has a linked employee and is not a staff role, treat as employee (e.g. role was set wrong)
    try:
        if getattr(user, 'employee', None) and role not in ('admin', 'hr', 'manager'):
            return 'routes.employee_dashboard'
    except Exception:
        pass
    return 'routes.dashboard'


def _require_admin_or_hr():
    """If current user is not Admin or HR (or Portal Admin), flash and return redirect; else return None."""
    from app.portal_config import is_admin_or_hr_role
    if is_admin_or_hr_role(getattr(current_user, 'role', None)):
        return None
    flash('Access denied. This section is for Admin and HR only.', 'error')
    return redirect(url_for(_dashboard_for_user(current_user)))


def _can_edit_employee_id() -> bool:
    """Only Admin (and Portal Admin) may change an existing employee_id."""
    from app.portal_config import is_admin_role
    return is_admin_role(getattr(current_user, 'role', None))


def _can_edit_employee_pds(emp):
    """Admin/HR may edit any PDS. All other roles may edit only their own record (Employee link or matching User.employee_id)."""
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role in ('admin', 'hr'):
        return True
    linked = getattr(current_user, 'employee', None)
    if linked is not None and linked.id == emp.id:
        return True
    uid = (getattr(current_user, 'employee_id', None) or '').strip()
    eid = (getattr(emp, 'employee_id', None) or '').strip()
    if uid and eid and uid == eid:
        return True
    return False


def _current_employee_for_user():
    """
    Return the Employee row for current_user, even if employees.user_id isn't linked yet.
    Fallback uses users.employee_id == employees.employee_id.
    """
    linked = getattr(current_user, 'employee', None)
    if linked is not None:
        return linked
    uid = (getattr(current_user, 'employee_id', None) or '').strip()
    if not uid:
        return None
    return Employee.query.filter_by(employee_id=uid).first()


def _managed_department_ids_for_manager():
    """Return list of department IDs managed by the current manager (may be empty)."""
    mgr_emp = _current_employee_for_user()
    if not mgr_emp:
        return []
    rows = Department.query.filter_by(manager_id=mgr_emp.id).all()
    return [d.id for d in rows if d and d.id]


def _public_landing_stats():
    """Aggregate counts for the public landing page (best-effort)."""
    stats = {'employees': 0, 'departments': 0, 'leave_requests': 0, 'positions': 0}
    try:
        stats['employees'] = Employee.query.filter(Employee.status == 'active').count()
    except Exception:
        try:
            stats['employees'] = Employee.query.count()
        except Exception:
            pass
    try:
        stats['departments'] = Department.query.count()
    except Exception:
        pass
    try:
        stats['leave_requests'] = LeaveRequest.query.count()
    except Exception:
        pass
    try:
        stats['positions'] = Position.query.count()
    except Exception:
        pass
    return stats


def _public_personnel_breakdown() -> dict:
    """
    Public landing helper: count plantilla vs non-plantilla personnel and provide
    a per-status breakdown (best-effort).

    We only include employees that are active to match the public
    "NUMBER OF PERSONNEL" card logic.
    """
    from sqlalchemy import func, or_

    def norm(col):
        return func.lower(func.trim(col))

    today = date.today()
    _ = today  # silence unused in some linters; kept for future date scoping

    plantilla_statuses = [
        ("Permanent", "permanent"),
        ("Casual", "casual"),
        ("Contractual", "contractual"),
        ("Temporary", "temporary"),
        ("Coterminus", "coterminus"),
        # Display label "Probational"; match new spelling and legacy "Provisional" in DB.
        ("Probational", ("probational", "provisional")),
        ("Elective", "elective"),
    ]
    nonplantilla_statuses = [
        ("COS", "contract of service"),
        ("JO", "job order"),
    ]

    base = Employee.query.filter(Employee.status == 'active')

    breakdown_plantilla: list[dict] = []
    breakdown_nonplantilla: list[dict] = []
    total_plantilla = 0
    total_nonplantilla = 0

    try:
        for label, key in plantilla_statuses:
            if isinstance(key, tuple):
                cond = or_(*[norm(Employee.status_of_appointment) == k for k in key])
                c = base.filter(cond).count()
            else:
                c = base.filter(norm(Employee.status_of_appointment) == key).count()
            breakdown_plantilla.append({"label": label, "count": int(c)})
            total_plantilla += int(c)

        for label, key in nonplantilla_statuses:
            c = base.filter(norm(Employee.status_of_appointment) == key).count()
            breakdown_nonplantilla.append({"label": label, "count": int(c)})
            total_nonplantilla += int(c)
    except Exception:
        breakdown_plantilla = [{"label": lbl, "count": 0} for (lbl, _) in plantilla_statuses]
        breakdown_nonplantilla = [{"label": lbl, "count": 0} for (lbl, _) in nonplantilla_statuses]
        total_plantilla = 0
        total_nonplantilla = 0

    return {
        "plantilla": {"total": total_plantilla, "items": breakdown_plantilla},
        "non_plantilla": {"total": total_nonplantilla, "items": breakdown_nonplantilla},
    }


def _public_birthdays_this_week(limit: int = 12) -> dict:
    """
    Public landing helper: return employees with birthdays in the current week.
    Week is Monday..Sunday in local server time. Uses EmployeePDS.date_of_birth
    and ignores the year component.
    """
    from sqlalchemy import or_

    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = week_start + timedelta(days=6)            # Sunday

    def safe_occurrence_for_year(y: int, m: int, d: int) -> date | None:
        try:
            return date(y, m, d)
        except ValueError:
            # Handle Feb 29 on non-leap years by mapping to Feb 28.
            if m == 2 and d == 29:
                return date(y, 2, 28)
            return None

    results: list[dict] = []
    try:
        rows = (db.session.query(Employee, EmployeePDS)
                .join(EmployeePDS, EmployeePDS.employee_id == Employee.id)
                .filter(EmployeePDS.date_of_birth.isnot(None))
                .filter(Employee.status == 'active')
                .all())

        for emp, pds in rows:
            dob = getattr(pds, 'date_of_birth', None)
            if not dob:
                continue

            occ = safe_occurrence_for_year(week_start.year, dob.month, dob.day)
            # If the week crosses year boundary, also check the next year.
            if occ is None or occ < week_start:
                occ2 = safe_occurrence_for_year(week_end.year, dob.month, dob.day)
                occ = occ2 if occ2 else occ

            if occ and week_start <= occ <= week_end:
                results.append({
                    'name': getattr(emp, 'full_name', None) or f"{emp.first_name} {emp.last_name}".strip(),
                    'when': occ,
                })

        results.sort(key=lambda r: (r['when'], r['name'].lower()))
    except Exception:
        # Best-effort only; landing page should still render.
        results = []

    items = results[: max(0, int(limit))]
    return {
        'week_start': week_start,
        'week_end': week_end,
        'items': items,
    }


def _public_on_leave_this_week(limit: int = 12) -> dict:
    """
    Public landing helper: return approved leave overlapping the current calendar month.

    Keys remain week_* for backwards compatibility with templates that reference them,
    but they represent inclusive month boundaries (first day .. last day).

    count is distinct employees with at least one overlapping approved request.
    """
    today = date.today()
    month_start = date(today.year, today.month, 1)
    last_dom = calendar.monthrange(today.year, today.month)[1]
    month_end = date(today.year, today.month, last_dom)

    total_count = 0
    results: list[dict] = []
    try:
        base_q = (db.session.query(LeaveRequest, Employee)
                  .join(Employee, LeaveRequest.employee_id == Employee.id)
                  .filter(LeaveRequest.status == 'approved')
                  .filter(LeaveRequest.start_date <= month_end)
                  .filter(LeaveRequest.end_date >= month_start))

        try:
            total_count = int(
                db.session.query(func.count(func.distinct(LeaveRequest.employee_id)))
                .join(Employee, LeaveRequest.employee_id == Employee.id)
                .filter(LeaveRequest.status == 'approved')
                .filter(LeaveRequest.start_date <= month_end)
                .filter(LeaveRequest.end_date >= month_start)
                .scalar()
                or 0
            )
        except Exception:
            total_count = 0

        rows = (base_q
                .order_by(LeaveRequest.start_date.asc(), Employee.last_name.asc(), Employee.first_name.asc())
                .limit(max(0, int(limit)))
                .all())

        for lr, emp in rows:
            results.append({
                'name': getattr(emp, 'full_name', None) or f"{emp.first_name} {emp.last_name}".strip(),
                'leave_type': (getattr(lr, 'leave_type', '') or '').strip(),
                'start': getattr(lr, 'start_date', None),
                'end': getattr(lr, 'end_date', None),
            })

        results.sort(key=lambda r: (r['start'] or month_start, r['name'].lower()))
    except Exception:
        total_count = 0
        results = []

    return {
        'week_start': month_start,
        'week_end': month_end,
        'count': total_count,
        'items': results,
    }


@bp.route('/')
def index():
    from app.portal_site_service import get_landing_cms
    return render_template(
        'landing.html',
        stats=_public_landing_stats(),
        birthdays=_public_birthdays_this_week(),
        on_leave=_public_on_leave_this_week(),
        personnel=_public_personnel_breakdown(),
        landing_cms=get_landing_cms(),
    )


@bp.route('/site-media/<path:filename>')
def site_media(filename):
    from app.portal_site_service import site_upload_dir
    safe = os.path.basename(filename)
    if safe != filename:
        abort(404)
    return send_from_directory(site_upload_dir(), safe)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    from app.portal_auth import is_portal_admin_session, start_portal_admin_session
    from app.portal_config import verify_portal_admin_credentials

    def _safe_post_login_redirect(next_page: str) -> str | None:
        """Allow only same-origin relative paths for known modules."""
        value = (next_page or '').strip()
        if not value.startswith('/') or value.startswith('//'):
            return None
        allowed_prefixes = (
            '/dashboard',
            '/employee-dashboard',
            '/portal-admin',
            '/ermt',
            '/payroll',
            '/leave',
            '/dtr',
            '/employees',
            '/reports',
            '/notifications',
            '/my-service-record',
        )
        if any(value == p or value.startswith(p + '/') or value.startswith(p + '?') for p in allowed_prefixes):
            return value
        return None

    if is_portal_admin_session() and request.method == 'GET' and not request.args.get('logged_out'):
        return redirect(url_for('routes.dashboard'))

    # If user came from logout (cookie may not have cleared), force show login and clear session again
    if request.args.get('logged_out'):
        logout_user()
        session.clear()
        session.modified = True
        if request.method == 'GET':
            return render_template('login.html')
    
    if request.method == 'POST':
        # Always logout current user on login attempt so new credentials are used (no "sticky" previous user)
        logout_user()
        session.clear()
        session.modified = True

        employee_id = request.form.get('employee_id', '').strip()
        password = request.form.get('password', '')
        
        if not employee_id or not password:
            flash('Please enter both ID Number and Password.', 'error')
            return render_template('login.html')

        if verify_portal_admin_credentials(employee_id, password):
            start_portal_admin_session()
            flash('Signed in with Portal Administrator and Admin access.', 'success')
            next_page = (request.args.get('next') or request.form.get('next') or '').strip()
            safe_next = _safe_post_login_redirect(next_page)
            if safe_next:
                return redirect(safe_next)
            return redirect(url_for('routes.dashboard'))

        # Find user by employee_id
        user = User.query.filter_by(employee_id=employee_id).first()

        if user and user.check_password(password) and user.is_active:
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()

            # Store user info in session
            session['user_name'] = user.employee.full_name if user.employee else user.username
            session['user_role'] = user.role

            flash(f'Welcome back, {employee_id}!', 'success')
            next_page = (request.args.get('next') or request.form.get('next') or '').strip()
            safe_next = _safe_post_login_redirect(next_page)
            if safe_next:
                return redirect(safe_next)
            return redirect(url_for(_dashboard_for_user(user)))
        else:
            flash('Invalid ID Number or Password.', 'error')
    
    # GET: show login form even if already authenticated (so user can switch account)
    return render_template('login.html')

@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    logout_user()
    session.clear()
    # Force session cookie to be dropped on client
    session.modified = True
    flash('You have been logged out successfully.', 'info')
    # Redirect to login with flag so login view can force-clear session if cookie didn't update
    return redirect(url_for('routes.index'), code=303)


@bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    from app.portal_auth import is_portal_admin_session
    if is_portal_admin_session():
        flash('Portal administrators cannot change password here.', 'error')
        return redirect(url_for('routes.dashboard'))
    current = request.form.get('current_password', '')
    new_pw = request.form.get('new_password', '')
    if not current:
        flash('Current password is required.', 'error')
        return redirect(url_for('routes.dashboard'))
    if not new_pw or len(new_pw) < 6:
        flash('New password must be at least 6 characters.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    if not current_user.check_password(current):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    current_user.set_password(new_pw)
    db.session.commit()
    flash('Password changed successfully.', 'success')
    return redirect(url_for(_dashboard_for_user(current_user)))


@bp.route('/employee-dashboard')
@login_required
def employee_dashboard():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    is_staff = role in ('admin', 'hr', 'manager')
    if role == 'employee' or (current_user.employee and not is_staff):
        pass  # allow
    else:
        return redirect(url_for('routes.dashboard'))
    employee = current_user.employee
    if not employee:
        flash('Employee record not linked to your account.', 'error')
        return redirect(url_for('routes.logout'))
    from app.employee_id_photo_service import employee_has_id_photo
    return render_template(
        'employee_dashboard.html',
        employee=employee,
        has_id_photo=employee_has_id_photo(employee),
    )


@bp.route('/employees/<int:id>/photo')
@login_required
def employee_id_photo(id):
    """Proxy ID photo bytes from the eRMT-linked NAS/local path."""
    from app.employee_id_photo_service import can_view_employee_id_photo, read_employee_id_photo

    emp = Employee.query.get_or_404(id)
    if not can_view_employee_id_photo(current_user, emp):
        abort(403)
    data, mime, _record = read_employee_id_photo(emp)
    if not data:
        abort(404)
    resp = Response(data, mimetype=mime or 'image/jpeg')
    resp.headers['Cache-Control'] = 'private, max-age=300'
    return resp


@bp.route('/my/photo')
@login_required
def my_id_photo():
    emp = _current_employee_for_user()
    if not emp:
        abort(404)
    return redirect(url_for('routes.employee_id_photo', id=emp.id))


@bp.route('/my-service-record')
@login_required
def my_service_record():
    """Read-only employment / appointment history for the logged-in employee."""
    emp = _current_employee_for_user()
    if not emp:
        flash('No employee record is linked to your account.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    emp, history, current_amount, si_due = _service_record_context(emp.id)
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    can_export_pdf = role in ('admin', 'hr')

    return render_template(
        'my_service_record.html',
        employee=emp,
        history_list=history,
        current_amount=current_amount,
        si_due=si_due,
        can_export_pdf=can_export_pdf,

        pdf_url=(
            url_for('routes.employee_service_record_pdf', id=emp.id)
            if can_export_pdf else None
        ),
        dashboard_url=url_for('routes.employee_dashboard'),
        page_title='My Service Record',
    )


def _service_record_context(employee_id: int) -> tuple[Employee, list[EmployeeAppointmentHistory], Decimal | None, date | None]:
    emp = (
        Employee.query
        .options(db.joinedload(Employee.department))
        .filter_by(id=employee_id)
        .first()
    )
    if not emp:
        raise ValueError('Employee not found')
    history = list(
        emp.appointment_history.order_by(
            EmployeeAppointmentHistory.effectivity_date.desc().nullslast(),
            EmployeeAppointmentHistory.timestamp.desc(),
            EmployeeAppointmentHistory.id.desc(),
        ).all()
    )
    current_amount = _current_salary_amount(emp)
    si_due = None
    try:
        si_due = _step_increment_due_date(emp, history_rows=history)
    except Exception:
        si_due = None
    return emp, history, current_amount, si_due


def _service_record_pdf_bytes(
    emp: Employee,
    history: list[EmployeeAppointmentHistory],
    current_amount: Decimal | None,
    si_due: date | None,
) -> io.BytesIO:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    page_w, page_h = A4
    margin = 28
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    # 3-column heading: logo (left), office text (center), balanced spacer (right).
    top_y = page_h - margin
    header_h = 54
    side_col_w = 72
    logo_size = 50
    header_bottom = top_y - header_h
    header_center_y = header_bottom + header_h / 2

    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'jagna_logo.png')
    left_col_center = margin + side_col_w / 2
    if os.path.exists(logo_path):
        logo_x = left_col_center - logo_size / 2
        logo_y = header_bottom + (header_h - logo_size) / 2
        c.drawImage(
            logo_path, logo_x, logo_y,
            width=logo_size, height=logo_size,
            preserveAspectRatio=True, mask='auto',
        )

    center_x = page_w / 2
    title_size, sub_size, line_gap = 14, 9.5, 0
    half_leading = (title_size + line_gap) / 2
    title_baseline = header_center_y + half_leading
    sub_baseline = header_center_y - half_leading
    c.setFont('Helvetica-Bold', title_size)
    c.drawCentredString(center_x, title_baseline, 'Municipality of Jagna')
    c.setFont('Helvetica', sub_size)
    c.drawCentredString(center_x, sub_baseline, 'Poblacion, Jagna, Bohol')

    sep_y = header_bottom - 10
    c.setStrokeColor(colors.HexColor('#0b3b6f'))
    c.setLineWidth(0.8)
    c.line(margin, sep_y, page_w - margin, sep_y)
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)

    c.setFont('Helvetica-Bold', 12)
    c.drawCentredString(center_x, sep_y - 18, 'EMPLOYEE SERVICE RECORD')

    name = emp.directory_name
    if emp.middle_name:
        name = f'{name} {emp.middle_name}'
    c.setFont('Helvetica', 7.5)
    one_liner = (
        f'Emp ID: {emp.employee_id or "—"}'
        f'   |   Dept: {emp.department.name if emp.department else "—"}'
        f'   |   Name: {name}'
        f'   |   Position: {emp.position or "—"}'
    )
    c.drawString(margin, sep_y - 38, one_liner)

    headers = ['Change Type', 'Effectivity', 'Status', 'Nature', 'SG', 'Step', 'From-To', 'Amount', 'Remarks']
    col_widths = [62, 50, 54, 58, 24, 24, 40, 46, 181]
    table_x = margin
    table_y = sep_y - 58
    row_h = 18

    c.setFillColor(colors.HexColor('#0b3b6f'))
    c.rect(table_x, table_y - row_h, sum(col_widths), row_h, fill=1, stroke=1)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 6.8)
    x = table_x
    for idx, head in enumerate(headers):
        c.drawString(x + 3, table_y - 14, head)
        x += col_widths[idx]

    c.setFillColor(colors.black)
    c.setFont('Helvetica', 6.6)
    y = table_y - row_h
    if not history:
        y -= row_h
        c.rect(table_x, y, sum(col_widths), row_h, fill=0, stroke=1)
        c.drawString(table_x + 3, y + 7, 'No appointment history has been recorded.')
    else:
        for h in history:
            y -= row_h
            if y < 90:
                c.showPage()
                y = page_h - margin - 30
                c.setFillColor(colors.HexColor('#0b3b6f'))
                c.rect(table_x, y - row_h, sum(col_widths), row_h, fill=1, stroke=1)
                c.setFillColor(colors.white)
                c.setFont('Helvetica-Bold', 6.8)
                x = table_x
                for idx, head in enumerate(headers):
                    c.drawString(x + 3, y - 14, head)
                    x += col_widths[idx]
                c.setFillColor(colors.black)
                c.setFont('Helvetica', 6.6)
                y -= row_h

            vals = [
                (h.change_type or h.appoint_nature or '—'),
                (h.effectivity_date.strftime('%Y-%m-%d') if h.effectivity_date else '—'),
                (h.appoint_status or '—'),
                (h.appoint_nature or '—'),
                (str(h.sal_grade) if h.sal_grade is not None else '—'),
                (str(h.sal_step) if h.sal_step is not None else '—'),
                (
                    f'{h.from_sal_step if h.from_sal_step is not None else "—"}→'
                    f'{h.to_sal_step if h.to_sal_step is not None else "—"}'
                ),
                (f'{h.sal_amount:,.2f}' if h.sal_amount is not None else '—'),
                (h.remarks or '—'),
            ]
            x = table_x
            for idx, v in enumerate(vals):
                c.rect(x, y, col_widths[idx], row_h, fill=0, stroke=1)
                raw = str(v)
                if len(raw) > 34 and idx in (8,):
                    raw = raw[:31] + '...'
                elif len(raw) > 14 and idx not in (8,):
                    raw = raw[:11] + '...'
                c.drawString(x + 2, y + 6, raw)
                x += col_widths[idx]

    sign_y = 52
    c.setFont('Helvetica', 9)
    c.line(page_w - 280, sign_y + 14, page_w - 80, sign_y + 14)
    c.drawCentredString(page_w - 180, sign_y, 'HRMDO Head')
    c.drawCentredString(page_w - 180, sign_y - 12, 'Signature over Printed Name')

    c.save()
    buf.seek(0)
    return buf


@bp.route('/employees/<int:id>/service-record')
@login_required
def employee_service_record(id: int):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp, history, current_amount, si_due = _service_record_context(id)
    return render_template(
        'my_service_record.html',
        employee=emp,
        history_list=history,
        current_amount=current_amount,
        si_due=si_due,
        can_export_pdf=True,
        pdf_url=url_for('routes.employee_service_record_pdf', id=emp.id),
        dashboard_url=url_for('routes.employees_list'),
        page_title='Employee Service Record',
    )


@bp.route('/employees/<int:id>/service-record/pdf')
@login_required
def employee_service_record_pdf(id: int):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp, history, current_amount, si_due = _service_record_context(id)
    buf = _service_record_pdf_bytes(emp, history, current_amount, si_due)
    fname = f'service_record_{(emp.employee_id or str(emp.id)).strip()}.pdf'
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=fname)


def _time_24_to_12(t):
    """Convert 24h time string (HH:MM or HH:MM:SS) to 12h e.g. '2:30 PM'. Returns '' if invalid."""
    if not t or not t.strip():
        return ''
    t = t.strip()
    parts = t.replace(':', ' ').split()
    if not parts or len(parts) < 2:
        return t
    try:
        h, m = int(parts[0]), int(parts[1])
        if h < 0 or h > 23 or m < 0 or m > 59:
            return t
        if h == 0:
            h12, am = 12, 'AM'
        elif h < 12:
            h12, am = h, 'AM'
        elif h == 12:
            h12, am = 12, 'PM'
        else:
            h12, am = h - 12, 'PM'
        return f'{h12}:{m:02d} {am}' if len(parts) == 2 else f'{h12}:{m:02d}:{int(parts[2]):02d} {am}'
    except (ValueError, IndexError):
        return t


def _is_jo_cos_employee(emp: Employee) -> bool:
    text = ((emp.status_of_appointment or '') + ' ' + (emp.nature_of_appointment or '')).strip().lower()
    return ('job order' in text) or ('contract of service' in text) or ('cos' in text) or text == 'jo'


def _is_cos_employee(emp: Employee) -> bool:
    """Contract of Service (aligned with plantilla for 4-day duty anchors). Not Job Order."""
    t = ((emp.status_of_appointment or '') + ' ' + (emp.nature_of_appointment or '')).strip().lower()
    if 'contract of service' in t:
        return True
    if 'job order' in t:
        return False
    return 'cos' in t or t == 'c.o.s.'


def _is_regular_employee(emp: Employee) -> bool:
    soa = (emp.status_of_appointment or '').strip().lower()
    if soa:
        return ('permanent' in soa) or ('regular' in soa)
    return not _is_jo_cos_employee(emp)


def _is_plantilla_leave_credits_employee(emp: Employee) -> bool:
    return (
        not _is_jo_cos_employee(emp)
        and (emp.status_of_appointment or '').strip() in LEAVE_CREDITS_STATUSES
    )


_DTR_DUTY_MODELS = {
    # 5-day compressed: 8:00 / 12:00 / 1:00 / 5:00
    '5day': {'am_in': (8, 0), 'am_out': (12, 0), 'pm_in': (13, 0), 'pm_out': (17, 0)},
    # 4-day compressed: 7:00 / 12:00 / 1:00 / 6:00
    '4day': {'am_in': (7, 0), 'am_out': (12, 0), 'pm_in': (13, 0), 'pm_out': (18, 0)},
}


def _time_to_minutes(t):
    if not t:
        return None
    return (t.hour * 60) + t.minute


def _parse_flexi_display_to_time(s):
    """Parse flexi_time_schedule.time_in / time_out display strings (e.g. 5:00 am) to time()."""
    if not s or not str(s).strip():
        return None
    s0 = ' '.join(str(s).strip().split())
    for cand in (s0, s0.replace(' ', '')):
        for fmt in ('%I:%M %p', '%I:%M%p', '%H:%M'):
            try:
                return datetime.strptime(cand, fmt).time()
            except ValueError:
                continue
    return None


def _parse_dtr_edit_time_field(raw):
    """Parse DTR edit form time (HTML time input or flexi-style display); blank -> None."""
    s = (raw or '').strip()
    if not s:
        return None
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return _parse_flexi_display_to_time(s)


def _duty_base_from_flexi_schedule_row(sch: FlexiTimeSchedule):
    if not sch:
        return None
    tin = _parse_flexi_display_to_time(sch.time_in)
    tout = _parse_flexi_display_to_time(sch.time_out)
    if not tin or not tout:
        return None
    base = dict(_DTR_DUTY_MODELS['5day'])
    base['am_in'] = (tin.hour, tin.minute)
    base['pm_out'] = (tout.hour, tout.minute)
    return base


def _duty_schedule_time_strings(base):
    """Format duty-model dict (am_in/am_out/pm_in/pm_out as (h,m) tuples) for DTR hints."""
    if not base:
        return '', '', '', ''

    def tup_to_str(tup):
        return time(tup[0], tup[1]).strftime('%I:%M %p').replace(' 0', ' ')

    return (
        tup_to_str(base['am_in']),
        tup_to_str(base['am_out']),
        tup_to_str(base['pm_in']),
        tup_to_str(base['pm_out']),
    )


def _prefetch_flexi_schedule_rows_for_range(emp: Employee, start_d: date, end_d: date) -> None:
    """Load flexi assignments for the viewed range in two queries instead of two per day."""
    if not emp or not getattr(emp, 'flexible_worktime', False) or not start_d or not end_d:
        return
    by_date: dict[date, FlexiTimeSchedule | None] = {}
    curr = start_d
    while curr <= end_d:
        by_date[curr] = None
        curr += timedelta(days=1)
    days = (
        EmployeeFlexiDay.query
        .filter(EmployeeFlexiDay.employee_id == emp.id)
        .filter(EmployeeFlexiDay.work_date >= start_d)
        .filter(EmployeeFlexiDay.work_date <= end_d)
        .all()
    )
    sids = {row.flexi_time_schedule_id for row in days if row.flexi_time_schedule_id}
    scheds = {}
    if sids:
        scheds = {
            s.id: s
            for s in FlexiTimeSchedule.query.filter(FlexiTimeSchedule.id.in_(sids)).all()
        }
    for row in days:
        by_date[row.work_date] = scheds.get(row.flexi_time_schedule_id)
    try:
        g._flexi_sched_lookup = {'emp_id': emp.id, 'by_date': by_date}
    except RuntimeError:
        pass


def _flexi_schedule_row_for_employee_date(emp: Employee, d: date):
    """FlexiTimeSchedule row for this calendar day, if employee is on flexi and has an assignment."""
    if not getattr(emp, 'flexible_worktime', False):
        return None
    try:
        look = getattr(g, '_flexi_sched_lookup', None)
    except RuntimeError:
        look = None
    if look and look.get('emp_id') == getattr(emp, 'id', None) and d in look.get('by_date', {}):
        return look['by_date'][d]
    day = EmployeeFlexiDay.query.filter_by(employee_id=emp.id, work_date=d).first()
    if not day or not day.flexi_time_schedule_id:
        return None
    return FlexiTimeSchedule.query.get(day.flexi_time_schedule_id)


def _flexi_scheduled_slot_time_strings(emp: Employee, d: date):
    """Expected AM/PM slot labels from flexi shift + standard lunch (5-day anchors), for DTR display."""
    sch = _flexi_schedule_row_for_employee_date(emp, d)
    if not sch:
        return '', '', '', ''
    base = _duty_base_from_flexi_schedule_row(sch)
    return _duty_schedule_time_strings(base)


def _parse_employee_flexi_days_form(raw_json: str):
    """Returns sorted list of (work_date, schedule_id). Raises ValueError on invalid payload."""
    raw = (raw_json or '').strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError('Invalid flexi-time dates JSON.') from e
    if not isinstance(data, list):
        raise ValueError('Invalid flexi-time dates JSON.')
    by_date = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        ds = (item.get('date') or '').strip()
        sid = item.get('schedule_id')
        if not ds or sid is None:
            continue
        try:
            d = datetime.strptime(ds, '%Y-%m-%d').date()
        except ValueError as e:
            raise ValueError(f'Invalid flexi date {ds!r}. Use YYYY-MM-DD.') from e
        try:
            sid_int = int(sid)
        except (ValueError, TypeError) as e:
            raise ValueError('Invalid flexi shift id in list.') from e
        if FlexiTimeSchedule.query.get(sid_int) is None:
            raise ValueError(
                'A flexi shift in the list no longer exists. Refresh the page and pick shifts from Settings → Flexi-time schedule.'
            )
        by_date[d] = sid_int
    return sorted(by_date.items(), key=lambda x: x[0])


def _derive_employee_default_flex_times_from_pairs(pairs):
    """Default Employee.flexible_* from earliest dated row's schedule (DTR fallback when no row for a day)."""
    if not pairs:
        return None, None
    first_sid = pairs[0][1]
    sch = FlexiTimeSchedule.query.get(first_sid)
    if not sch:
        return None, None
    tin = _parse_flexi_display_to_time(sch.time_in)
    tout = _parse_flexi_display_to_time(sch.time_out)
    return tin, tout


def _sync_employee_flexi_days(employee_id: int, pairs):
    EmployeeFlexiDay.query.filter_by(employee_id=employee_id).delete(synchronize_session=False)
    for d, sid in pairs:
        db.session.add(
            EmployeeFlexiDay(
                employee_id=employee_id,
                work_date=d,
                flexi_time_schedule_id=sid,
            )
        )


def _flexi_schedules_serialized():
    rows = FlexiTimeSchedule.query.order_by(FlexiTimeSchedule.sort_order, FlexiTimeSchedule.shift_code).all()
    return [
        {'id': r.id, 'shift_code': r.shift_code, 'time_in': r.time_in, 'time_out': r.time_out}
        for r in rows
    ]


def _employee_flexi_days_initial_list(emp):
    if not emp:
        return []
    rows = (
        EmployeeFlexiDay.query.filter_by(employee_id=emp.id)
        .order_by(EmployeeFlexiDay.work_date, EmployeeFlexiDay.id)
        .all()
    )
    return [{'date': r.work_date.isoformat(), 'schedule_id': r.flexi_time_schedule_id} for r in rows]


def _jo_cos_rate_form_kwargs():
    """Data for employee form: JO/COS per-day amount from jo_cos_rate (+ id=1 fallback)."""
    rows = JoCosRate.query.order_by(JoCosRate.sort_order, JoCosRate.id).all()
    basic = JoCosRate.query.get(1)
    return {
        'jo_cos_rates_for_js': [
            {
                'id': r.id,
                'status_of_appointment': r.status_of_appointment,
                'designation_label': r.designation_label,
                'rate_per_day': str(r.rate_per_day),
            }
            for r in rows
        ],
        'jo_cos_basic_rate_per_day': str(basic.rate_per_day) if basic else None,
    }


def _parse_employee_separation_type(raw):
    value = (raw or '').strip()
    if not value:
        return None
    if value not in EMPLOYEE_SEPARATION_TYPES:
        raise ValueError(f"Invalid separation type. Allowed: {', '.join(EMPLOYEE_SEPARATION_TYPES)}.")
    return value


def _employee_form_flexi_kwargs(emp=None):
    return {
        **_jo_cos_rate_form_kwargs(),
        'employee_separation_types': EMPLOYEE_SEPARATION_TYPES,
    }


def _flexible_worktime_rows_for_employee(emp_id: int):
    """All quincena Flexible Worktime rows for an employee, cached per request."""
    if not emp_id:
        return []
    try:
        cache = g._flexi_worktime_rows_cache
    except (AttributeError, RuntimeError):
        cache = None
    if cache is None:
        cache = {}
        try:
            g._flexi_worktime_rows_cache = cache
        except RuntimeError:
            pass
    if emp_id not in cache:
        cache[emp_id] = (
            FlexibleWorktime.query
            .filter_by(employee_id=emp_id)
            .order_by(FlexibleWorktime.date_start.asc(), FlexibleWorktime.id.asc())
            .all()
        )
    return cache[emp_id]


def _flexible_worktime_row_for_date(emp: Employee, d: date):
    """Quincena Flexible Worktime row covering this calendar date, if any."""
    if not emp or not d:
        return None
    match = None
    for row in _flexible_worktime_rows_for_employee(getattr(emp, 'id', None)):
        if row.date_start <= d <= row.date_end:
            match = row
    return match


def _flexible_worktime_duty_schedule_for_date(emp: Employee, d: date):
    """Duty schedule from the Flexible Worktime list for this date (None when not listed)."""
    row = _flexible_worktime_row_for_date(emp, d)
    if not row:
        return None
    return _duty_schedule_from_flexible_worktime_row(row)


def _employee_is_flexible_for_date(emp: Employee, d: date) -> bool:
    """
    True when the employee is flexible for this date, from either source:
    the Employee.flexible_worktime flag or the quincena Flexible Worktime list.
    """
    if bool(getattr(emp, 'flexible_worktime', False)):
        return True
    return _flexible_worktime_row_for_date(emp, d) is not None


def _pick_work_arrangement_for_date(emp: Employee, d: date):
    """Pick a date-effective arrangement; defaults to 5-day when none matched."""
    # Quincena Flexible Worktime list wins: it carries explicit in/break/out targets.
    flexi_list_sched = _flexible_worktime_duty_schedule_for_date(emp, d)
    if flexi_list_sched:
        return flexi_list_sched

    if bool(getattr(emp, 'flexible_worktime', False)):
        sch = _flexi_schedule_row_for_employee_date(emp, d)
        if sch:
            base = _duty_base_from_flexi_schedule_row(sch)
            if base:
                return base
        flex_start = getattr(emp, 'flexible_start_time', None)
        flex_end = getattr(emp, 'flexible_end_time', None)
        base = dict(_DTR_DUTY_MODELS['5day'])
        if flex_start:
            base['am_in'] = (flex_start.hour, flex_start.minute)
        if flex_end:
            base['pm_out'] = (flex_end.hour, flex_end.minute)
        return base

    cal = calendar_model_code_for_date(d)
    if cal in _DTR_DUTY_MODELS:
        return dict(_DTR_DUTY_MODELS[cal])

    is_regular = _is_regular_employee(emp)
    rows = dtr_wa.work_arrangement_settings_covering(d)
    for row in rows:
        applies = (row.applies_to or 'all').strip().lower()
        if applies == 'all':
            return _DTR_DUTY_MODELS.get(row.model_code) or _DTR_DUTY_MODELS['5day']
        if applies == 'regular' and is_regular:
            return _DTR_DUTY_MODELS.get(row.model_code) or _DTR_DUTY_MODELS['5day']
        if applies == 'jo_cos' and not is_regular:
            return _DTR_DUTY_MODELS.get(row.model_code) or _DTR_DUTY_MODELS['5day']
    return _DTR_DUTY_MODELS['5day']


def _employee_matches_dtr_applies_to(emp: Employee, applies_to: str) -> bool:
    applies = (applies_to or 'all').strip().lower()
    if applies == 'all':
        return True
    if applies == 'regular':
        return _is_regular_employee(emp)
    if applies == 'jo_cos':
        return _is_jo_cos_employee(emp)
    return True


def _arrangement_model_code_for_date(emp: Employee, d: date) -> str:
    """Effective duty model code for late/undertime (e.g. '4day', '5day'); 'flex' if flexible_worktime."""
    if _employee_is_flexible_for_date(emp, d):
        return 'flex'
    cal = calendar_model_code_for_date(d)
    if cal in ('4day', '5day'):
        return cal
    is_regular = _is_regular_employee(emp)
    rows = dtr_wa.work_arrangement_settings_covering(d)
    for row in rows:
        applies = (row.applies_to or 'all').strip().lower()
        if applies == 'all' or (applies == 'regular' and is_regular) or (applies == 'jo_cos' and not is_regular):
            return ((row.model_code or '5day').strip().lower() or '5day')
    return '5day'


def _pick_late_undertime_schedule(emp: Employee, d: date):
    """Schedule used only for late/undertime computation (true 4-day / 5-day / flexi)."""
    return _pick_work_arrangement_for_date(emp, d)


def _late_undertime_minutes_for_record(rec, schedule):
    if not rec or not schedule:
        return 0, 0
    if _duty_schedule_is_rest(schedule):
        return 0, 0

    target_am_in = schedule['am_in'][0] * 60 + schedule['am_in'][1]
    target_am_out = schedule['am_out'][0] * 60 + schedule['am_out'][1]
    target_pm_in = schedule['pm_in'][0] * 60 + schedule['pm_in'][1]
    target_pm_out = schedule['pm_out'][0] * 60 + schedule['pm_out'][1]

    am_in = _time_to_minutes(rec.am_in)
    am_out = _time_to_minutes(rec.am_out)
    pm_in = _time_to_minutes(rec.pm_in)
    pm_out = _time_to_minutes(rec.pm_out)

    # Straight duty (no meal break): only In and Out matter.
    if _duty_schedule_is_straight(schedule):
        late_mins = 0
        undertime_mins = 0
        punch_in = am_in if am_in is not None else pm_in
        punch_out = pm_out if pm_out is not None else am_out
        if punch_in is not None and punch_in > target_am_in:
            late_mins += punch_in - target_am_in
        if punch_out is not None:
            if target_pm_out < target_am_in:
                eff_target = target_pm_out + 24 * 60
                eff_actual = punch_out + (24 * 60 if punch_out < target_am_in else 0)
                if eff_actual < eff_target:
                    undertime_mins += eff_target - eff_actual
            elif punch_out < target_pm_out:
                undertime_mins += target_pm_out - punch_out
        return late_mins, undertime_mins

    # Overnight-style flexi (e.g. 8:00 pm → 5:00 am): duty end is "earlier" on clock than start.
    if target_pm_out < target_am_in:
        late_mins = 0
        undertime_mins = 0
        if am_in is not None and am_in > target_am_in:
            late_mins += am_in - target_am_in
        if pm_out is not None:
            eff_target = target_pm_out + 24 * 60
            eff_actual = pm_out + (24 * 60 if pm_out < target_am_in else 0)
            if eff_actual < eff_target:
                undertime_mins += eff_target - eff_actual
        return late_mins, undertime_mins

    late_mins = 0
    undertime_mins = 0
    if am_in is not None and am_in > target_am_in:
        late_mins += (am_in - target_am_in)
    if pm_in is not None and pm_in > target_pm_in:
        late_mins += (pm_in - target_pm_in)
    if am_out is not None and am_out < target_am_out:
        undertime_mins += (target_am_out - am_out)
    if pm_out is not None and pm_out < target_pm_out:
        undertime_mins += (target_pm_out - pm_out)
    return late_mins, undertime_mins


def _duty_schedule_is_straight(sched) -> bool:
    return ((sched or {}).get('duty_mode') or 'split').strip().lower() == 'straight'


def _duty_schedule_is_rest(sched) -> bool:
    return ((sched or {}).get('duty_mode') or '').strip().lower() == 'rest'


def _scheduled_shift_minutes_from_duty_schedule(sched):
    """
    Expected duty length in minutes for one calendar DTR row (same dict shape as 5day / flexi duty base).

    Rest mode: 0 (scheduled day off).
    Straight mode: continuous In → Out (supports overnight when Out < In).
    Split / overnight flex: two segments (in → break-out, break-in → out) with midnight wrap where needed.
    """
    if not sched:
        return 8 * 60
    if _duty_schedule_is_rest(sched):
        return 0
    t_am_in = sched['am_in'][0] * 60 + sched['am_in'][1]
    t_am_out = sched['am_out'][0] * 60 + sched['am_out'][1]
    t_pm_in = sched['pm_in'][0] * 60 + sched['pm_in'][1]
    t_pm_out = sched['pm_out'][0] * 60 + sched['pm_out'][1]
    if _duty_schedule_is_straight(sched):
        if t_pm_out < t_am_in:
            return max(0, (24 * 60 - t_am_in) + t_pm_out)
        return max(0, t_pm_out - t_am_in)
    if t_pm_out < t_am_in:
        if t_am_out < t_am_in:
            seg_am = max(0, (24 * 60 - t_am_in) + t_am_out)
        else:
            seg_am = max(0, t_am_out - t_am_in)
        if t_pm_out < t_pm_in:
            seg_pm = max(0, (24 * 60 - t_pm_in) + t_pm_out)
        else:
            seg_pm = max(0, t_pm_out - t_pm_in)
        return seg_am + seg_pm
    return max(0, t_am_out - t_am_in) + max(0, t_pm_out - t_pm_in)


def _duty_schedule_is_overnight(sched) -> bool:
    if not sched or _duty_schedule_is_rest(sched):
        return False
    t_am_in = sched['am_in'][0] * 60 + sched['am_in'][1]
    t_pm_out = sched['pm_out'][0] * 60 + sched['pm_out'][1]
    return t_pm_out < t_am_in


def _scheduled_segment_minutes_from_duty_schedule(sched, segment: str) -> int:
    """AM or PM segment length in minutes from a duty schedule dict."""
    if not sched:
        return 0
    if _duty_schedule_is_straight(sched):
        # Half-day holiday/suspension on a straight shift: half of full duty minutes.
        return _scheduled_shift_minutes_from_duty_schedule(sched) // 2
    if segment == 'AM':
        t_in = sched['am_in'][0] * 60 + sched['am_in'][1]
        t_out = sched['am_out'][0] * 60 + sched['am_out'][1]
    else:
        t_in = sched['pm_in'][0] * 60 + sched['pm_in'][1]
        t_out = sched['pm_out'][0] * 60 + sched['pm_out'][1]
    return max(0, t_out - t_in)


def _worked_minutes_within_flexi_schedule(rec, schedule) -> int:
    """
    Minutes credited within flexi schedule windows only (in / break-out / break-in / out).
    Punches before time-in or after time-out do not add credit beyond the allowable shift.
    Straight mode credits a single In → Out window.
    """
    if not rec or not schedule:
        return 0
    t_am_in = schedule['am_in'][0] * 60 + schedule['am_in'][1]
    t_am_out = schedule['am_out'][0] * 60 + schedule['am_out'][1]
    t_pm_in = schedule['pm_in'][0] * 60 + schedule['pm_in'][1]
    t_pm_out = schedule['pm_out'][0] * 60 + schedule['pm_out'][1]

    am_in = _time_to_minutes(rec.am_in)
    am_out = _time_to_minutes(rec.am_out)
    pm_in = _time_to_minutes(rec.pm_in)
    pm_out = _time_to_minutes(rec.pm_out)

    if not any(v is not None for v in (am_in, am_out, pm_in, pm_out)):
        return 0

    full_sched = _scheduled_shift_minutes_from_duty_schedule(schedule)

    if _duty_schedule_is_straight(schedule):
        punch_in = am_in if am_in is not None else pm_in
        punch_out = pm_out if pm_out is not None else am_out
        if punch_in is None and punch_out is None:
            return 0
        eff_in = punch_in if punch_in is not None else t_am_in
        eff_out = punch_out if punch_out is not None else t_pm_out
        start = max(eff_in, t_am_in)
        end = min(eff_out, t_pm_out)
        if t_pm_out < t_am_in:
            # Overnight straight: wrap past midnight.
            if punch_out is not None and punch_out < t_am_in:
                end = punch_out + 24 * 60
                start = max(eff_in, t_am_in)
                end = min(end, t_pm_out + 24 * 60)
                total = max(0, end - start)
            else:
                # Incomplete overnight: credit whatever falls inside either side of midnight
                total = 0
                if punch_in is not None:
                    total += max(0, (24 * 60) - max(punch_in, t_am_in))
                if punch_out is not None:
                    total += max(0, min(punch_out, t_pm_out) - 0)
            return min(total, full_sched)
        if end > start:
            return min(end - start, full_sched)
        return 0

    if t_pm_out < t_am_in:
        total = 0
        if am_in is not None or am_out is not None:
            eff_in = am_in if am_in is not None else t_am_in
            eff_out = am_out if am_out is not None else t_am_out
            start = max(eff_in, t_am_in)
            end = min(eff_out, t_am_out)
            if t_am_out < t_am_in:
                if end >= start:
                    total += end - start
                else:
                    total += max(0, (24 * 60 - start) + end)
            elif end > start:
                total += end - start
        if pm_in is not None or pm_out is not None:
            eff_in = pm_in if pm_in is not None else t_pm_in
            eff_out = pm_out if pm_out is not None else t_pm_out
            start = max(eff_in, t_pm_in)
            end = min(eff_out, t_pm_out)
            if t_pm_out < t_pm_in:
                if end >= start:
                    total += end - start
                else:
                    total += max(0, (24 * 60 - start) + end)
            elif end > start:
                total += end - start
        return min(total, full_sched)

    total = 0
    two_punch = (
        am_in is not None
        and pm_out is not None
        and am_out is None
        and pm_in is None
    )

    if am_in is not None or am_out is not None:
        eff_in = am_in if am_in is not None else t_am_in
        eff_out = am_out if am_out is not None else t_am_out
        start = max(eff_in, t_am_in)
        end = min(eff_out, t_am_out)
        if end > start:
            total += end - start
    elif two_punch and am_in <= t_am_out:
        total += max(0, t_am_out - max(am_in, t_am_in))

    if pm_in is not None or pm_out is not None:
        if not two_punch or pm_in is not None:
            eff_in = pm_in if pm_in is not None else t_pm_in
            eff_out = pm_out if pm_out is not None else t_pm_out
            start = max(eff_in, t_pm_in)
            end = min(eff_out, t_pm_out)
            if end > start:
                total += end - start
        elif two_punch and pm_out >= t_pm_in:
            total += max(0, min(pm_out, t_pm_out) - t_pm_in)

    return min(total, full_sched)


def _dtr_normalize_remarks(remarks: str | None) -> str:
    """Map DTR remark variants to canonical labels used in worktime rules."""
    r = (remarks or '').strip()
    if r.upper() == 'LEAVE':
        return 'APPROVED LEAVE'
    return r


def _dtr_is_paid_leave_remark(remarks: str | None) -> bool:
    r = _dtr_normalize_remarks(remarks).strip().upper()
    return r == 'APPROVED LEAVE'


def _dtr_gross_minutes_for_flexi_day(rec, schedule, remarks: str) -> int:
    """Gross worktime for quincena flexible_worktime rows — capped to schedule windows."""
    if _duty_schedule_is_rest(schedule):
        return 0
    r = _dtr_normalize_remarks(remarks).strip().upper()
    if r == 'REST DAY':
        return 0
    full_sched = _scheduled_shift_minutes_from_duty_schedule(schedule)
    if r in ('APPROVED LEAVE', 'HOLIDAY', 'WORK SUSPENSION'):
        return full_sched
    if r in ('HOLIDAY AM', 'WORK SUSPENSION AM'):
        return _scheduled_segment_minutes_from_duty_schedule(schedule, 'AM')
    if r in ('HOLIDAY PM', 'WORK SUSPENSION PM'):
        return _scheduled_segment_minutes_from_duty_schedule(schedule, 'PM')
    if not rec or not any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]):
        return 0
    return _worked_minutes_within_flexi_schedule(rec, schedule)


DTR_EIGHT_HOUR_DAY_MINS = 480  # 8-hour day; 10 × 480 = 80h quincena equivalent


def _dtr_rest_friday_payable_holiday_credit_mins(remarks: str | None) -> int:
    """Payable minutes for a 4-day REST Friday that is holiday / leave / suspension (8h basis)."""
    r = _dtr_normalize_remarks(remarks).strip().upper()
    if r in ('HOLIDAY', 'WORK SUSPENSION', 'APPROVED LEAVE'):
        return DTR_EIGHT_HOUR_DAY_MINS
    if r in ('HOLIDAY AM', 'WORK SUSPENSION AM', 'HOLIDAY PM', 'WORK SUSPENSION PM'):
        return DTR_EIGHT_HOUR_DAY_MINS // 2
    return 0


def _dtr_fourday_friday_rest_no_credit(emp: Employee | None, d: date | None, rec, remarks: str) -> bool:
    """
    4-day compressed: REST Friday without punches stays at 0 in the first pass.
    Holiday/leave/suspension on that Friday is credited later via
    `_dtr_rebalance_fourday_rest_friday_holiday_credits` (8h reallocated from Mon-Thu).
    """
    if not emp or not d or d.weekday() != 4:
        return False
    if _arrangement_model_code_for_date(emp, d) != '4day':
        return False
    if rec and any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]):
        return False
    return _dtr_is_special_day_remark(remarks)


def _dtr_rebalance_fourday_rest_friday_holiday_credits(day_rows: list[dict], emp: Employee) -> None:
    """
    Credit holiday/leave/suspension on 4-day REST Fridays as one 8-hour day (480 min),
    reallocated from Mon-Thu when the quincena already has a full 80-hour Mon-Thu base.
    """
    holiday_rows: list[tuple[dict, int]] = []
    for row in day_rows:
        d = row['work_date']
        if d.weekday() != 4:
            continue
        if _arrangement_model_code_for_date(emp, d) != '4day':
            continue
        if row.get('gross_work_mins', 0) > 0:
            continue
        credit = _dtr_rest_friday_payable_holiday_credit_mins(row.get('remarks'))
        if credit > 0:
            holiday_rows.append((row, credit))

    if not holiday_rows:
        return

    montu_rows = [
        r for r in day_rows
        if r['work_date'].weekday() < 4 and r.get('gross_work_mins', 0) > 0
    ]
    if not montu_rows:
        return

    raw_credit = sum(c for _, c in holiday_rows)
    montu_total = sum(r['gross_work_mins'] for r in montu_rows)
    total_credit = min(raw_credit, montu_total)

    assigned = 0
    for i, (row, credit) in enumerate(holiday_rows):
        if i == len(holiday_rows) - 1:
            row_credit = total_credit - assigned
        else:
            row_credit = (total_credit * credit) // raw_credit
            assigned += row_credit
        row['gross_work_mins'] = row_credit

    remaining = total_credit
    n = len(montu_rows)
    per = remaining // n
    extra = remaining % n
    for i, row in enumerate(montu_rows):
        deduct = min(row['gross_work_mins'], per + (1 if i < extra else 0))
        row['gross_work_mins'] -= deduct

    jo_cos = _is_jo_cos_employee(emp)
    for row in day_rows:
        if jo_cos:
            row['net_rendered_mins'] = max(
                0,
                row['gross_work_mins']
                - row['late_mins']
                - row['undertime_mins']
                - row['absence_mins'],
            )
        else:
            row['net_rendered_mins'] = row['gross_work_mins']


def _dtr_gross_minutes_for_day(
    rec, schedule, remarks: str, is_weekend: bool, *, emp: Employee | None = None, d: date | None = None
) -> int:
    """
    Scheduled duty minutes credited for one calendar day (gross worktime base).

    Present days, approved leave, holidays, and work suspensions receive full or
    half-day scheduled credit. REST DAY and weekends receive 0. Unexcused absences
    receive 0 here and are deducted separately via absence_mins.
    4-day REST Friday holidays/leave/suspensions without punches start at 0 here and
    receive 8-hour credit via `_dtr_rebalance_fourday_rest_friday_holiday_credits`.
    """
    if _dtr_fourday_friday_rest_no_credit(emp, d, rec, remarks):
        return 0
    if is_weekend or not schedule:
        return 0
    r = _dtr_normalize_remarks(remarks).strip().upper()
    if r == 'REST DAY':
        return 0
    full_sched = _scheduled_shift_minutes_from_duty_schedule(schedule)
    if r in ('APPROVED LEAVE', 'HOLIDAY', 'WORK SUSPENSION'):
        return full_sched
    if r in ('HOLIDAY AM', 'WORK SUSPENSION AM'):
        return _scheduled_segment_minutes_from_duty_schedule(schedule, 'AM')
    if r in ('HOLIDAY PM', 'WORK SUSPENSION PM'):
        return _scheduled_segment_minutes_from_duty_schedule(schedule, 'PM')
    if rec and any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]):
        return full_sched
    return 0


def _approved_leave_dates_for_employee(emp_id: int, start_d: date, end_d: date) -> set[date]:
    dates = set()
    leaves = (LeaveRequest.query
              .filter(LeaveRequest.employee_id == emp_id)
              .filter(LeaveRequest.status == 'approved')
              .filter(LeaveRequest.start_date <= end_d)
              .filter(LeaveRequest.end_date >= start_d)
              .all())
    for lv in leaves:
        curr = max(lv.start_date, start_d)
        stop = min(lv.end_date, end_d)
        while curr <= stop:
            dates.add(curr)
            curr += timedelta(days=1)
    return dates


def _worked_minutes_from_record(rec):
    if not rec:
        return 0
    am_in = _time_to_minutes(rec.am_in)
    am_out = _time_to_minutes(rec.am_out)
    pm_in = _time_to_minutes(rec.pm_in)
    pm_out = _time_to_minutes(rec.pm_out)
    # One-row overnight span (e.g. 8:00 pm in, 5:00 am out) — two lunch segments do not apply.
    if (
        am_in is not None
        and pm_out is not None
        and pm_out < am_in
        and am_out is None
        and pm_in is None
    ):
        return max(0, (24 * 60 - am_in) + pm_out)

    mins = 0
    if rec.am_in and rec.am_out:
        a = _time_to_minutes(rec.am_in)
        b = _time_to_minutes(rec.am_out)
        if a is not None and b is not None and b > a:
            mins += (b - a)
    if rec.pm_in and rec.pm_out:
        a = _time_to_minutes(rec.pm_in)
        b = _time_to_minutes(rec.pm_out)
        if a is not None and b is not None and b > a:
            mins += (b - a)
    return mins


_JUSTIFICATION_ALLOWED_EXTS = {'.pdf', '.png', '.jpg', '.jpeg', '.doc', '.docx'}


def _justification_upload_dir():
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'justifications'))
    os.makedirs(base, exist_ok=True)
    return base


def _is_allowed_justification_filename(name: str) -> bool:
    _, ext = os.path.splitext((name or '').lower())
    return ext in _JUSTIFICATION_ALLOWED_EXTS


def _dtr_justification_reason_from_form(form) -> tuple[str | None, str | None]:
    """Return (reason text, error message)."""
    category = (form.get('reason_category') or '').strip()
    if category not in DTR_JUSTIFICATION_REASONS:
        return None, 'Please select a valid reason.'
    if category == DTR_JUSTIFICATION_REASON_OTHERS:
        other = (form.get('reason_other') or '').strip()
        if not other:
            return None, 'Please provide details when reason is Others.'
        return f'Others — {other}', None
    return category, None


def _redirect_after_dtr_justify_error(role: str, form) -> Response:
    rec_date_raw = (form.get('record_date') or '').strip()
    if (role or '').strip().lower() == 'employee' and rec_date_raw:
        try:
            rec_date = datetime.strptime(rec_date_raw, '%Y-%m-%d').date()
        except ValueError:
            return redirect(url_for('routes.dtr_my_records'))
        mode = (form.get('justify_mode') or 'quincena').strip().lower()
        if mode not in ('quincena', 'month'):
            mode = 'quincena'
        quincena = (form.get('justify_quincena') or '1').strip()
        return redirect(url_for(
            'routes.dtr_my_records',
            year=rec_date.year,
            month=rec_date.month,
            mode=mode,
            quincena=quincena,
            justify_date=rec_date_raw,
        ))
    return redirect(url_for('routes.dtr_justifications'))


def _month_date_range(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _quincena_date_range(year: int, month: int, quincena: str) -> tuple[date, date]:
    return quincena_core_date_range(year, month, quincena)


def _build_quincena_upload_options(anchor: date):
    opts = []
    for i in range(0, 4):
        m = anchor.month - i
        y = anchor.year
        while m <= 0:
            m += 12
            y -= 1
        month_name = date(y, m, 1).strftime('%B')
        opts.append({
            'value': f'{y:04d}-{m:02d}-1',
            'label': f'{month_name} - 1st Quincena',
        })
        opts.append({
            'value': f'{y:04d}-{m:02d}-2',
            'label': f'{month_name} - 2nd Quincena',
        })
    return opts


def _parse_quincena_upload_value(raw: str):
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


def _missing_dates_for_quincena(row_dates: set[date], year: int, month: int, quincena: str):
    start_d, end_d = _quincena_date_range(year, month, quincena)
    missing = []
    curr = start_d
    while curr <= end_d:
        if curr not in row_dates:
            missing.append(curr)
        curr += timedelta(days=1)
    return missing


def _row_dates_from_upload_payload(row_data: list[dict]) -> set[date]:
    dates = set()
    for row in row_data:
        rec_date = _parse_dtr_upload_form_date(str(row.get('date') or '').strip())
        if rec_date is not None:
            dates.add(rec_date)
    return dates


def _row_dates_in_core_quincena(
    row_data: list[dict],
    year: int,
    month: int,
    quincena: str,
) -> set[date]:
    """Dates from upload rows that fall inside the quincena itself (not buffer days)."""
    core_start, core_end = _quincena_date_range(year, month, quincena)
    dates = set()
    for row in row_data:
        rec_date = _parse_dtr_upload_form_date(str(row.get('date') or '').strip())
        if rec_date is not None and core_start <= rec_date <= core_end:
            dates.add(rec_date)
    return dates


def _filter_upload_rows_for_quincena(row_data: list[dict], upload_quincena: str):
    """
    Keep rows whose date falls inside the selected quincena, plus one buffer day
    before and after (night shift / flexitime).
    Returns (filtered_rows, outside_dates, skipped_row_count, error_message).
    """
    qy, qm, qh = _parse_quincena_upload_value(upload_quincena)
    if qy is None:
        return [], [], 0, 'Invalid quincena selection.'
    accept_start, accept_end, _core_start, _core_end = quincena_upload_accept_range(qy, qm, qh)
    filtered = []
    outside_dates = set()
    skipped = 0
    for row in row_data:
        rec_date = _parse_dtr_upload_form_date(str(row.get('date') or '').strip())
        if rec_date is not None and (rec_date < accept_start or rec_date > accept_end):
            outside_dates.add(rec_date)
            skipped += 1
            continue
        filtered.append(row)
    return filtered, sorted(outside_dates), skipped, None


def _dtr_upload_batch_label(batch: DtrUploadBatch) -> str:
    qy, qm, qh = _parse_quincena_upload_value(batch.quincena or '')
    if qy is not None:
        return _jo_cos_payslip_period_label(qy, qm, str(qh))
    return batch.quincena or '—'


def _resolve_dtr_am_out_pm_in_conflict(am_out, pm_in):
    """If AM break-out and PM break-in cross, align timestamps (same employee/date)."""
    if am_out is None or pm_in is None:
        return am_out, pm_in
    if am_out > pm_in:
        pm_in = am_out
    if pm_in < am_out:
        am_out = pm_in
    return am_out, pm_in


DTR_UPLOAD_MERGE_FILL_BLANKS = 'fill_blanks'
DTR_UPLOAD_MERGE_MINMAX = 'merge_minmax'
DTR_UPLOAD_MERGE_STRATEGIES = frozenset({DTR_UPLOAD_MERGE_FILL_BLANKS, DTR_UPLOAD_MERGE_MINMAX})


def _merge_two_dtr_times_pick_min(existing, new):
    """When both clocks exist, keep earlier (arrival / back-from-break style)."""
    if new is None:
        return existing
    if existing is None:
        return new
    return existing if existing <= new else new


def _merge_two_dtr_times_pick_max(existing, new):
    """When both clocks exist, keep later (departure / end-of-interval style)."""
    if new is None:
        return existing
    if existing is None:
        return new
    return existing if existing >= new else new


def _merge_two_dtr_times_fill_blank(existing, new):
    """Never change a stored punch; only fill when the DB slot is blank."""
    return existing if existing is not None else new


def _normalize_dtr_upload_merge_strategy(raw) -> str:
    s = (raw or '').strip().lower()
    if s in DTR_UPLOAD_MERGE_STRATEGIES:
        return s
    return DTR_UPLOAD_MERGE_FILL_BLANKS


def _merge_dtr_times_with_upload(existing_am_in, existing_am_out, existing_pm_in, existing_pm_out,
                                   upload_am_in, upload_am_out, upload_pm_in, upload_pm_out,
                                   strategy=DTR_UPLOAD_MERGE_FILL_BLANKS):
    """
    Merge stored DTR with upload.
    fill_blanks (default): only fill None slots; never change an existing punch,
    except generated duty In (7/8 AM) / Out (5/6 PM) which yield to actual timestamps.
    merge_minmax: fill blanks; when both set, keep earlier in / later out.
    """
    strategy = _normalize_dtr_upload_merge_strategy(strategy)
    if strategy == DTR_UPLOAD_MERGE_MINMAX:
        am_in = _merge_two_dtr_times_pick_min(existing_am_in, upload_am_in)
        am_out = _merge_two_dtr_times_pick_max(existing_am_out, upload_am_out)
        pm_in = _merge_two_dtr_times_pick_min(existing_pm_in, upload_pm_in)
        pm_out = _merge_two_dtr_times_pick_max(existing_pm_out, upload_pm_out)
    else:
        am_in = merge_prefer_actual_over_duty_stamp(existing_am_in, upload_am_in, 'am_in')
        am_out = _merge_two_dtr_times_fill_blank(existing_am_out, upload_am_out)
        pm_in = _merge_two_dtr_times_fill_blank(existing_pm_in, upload_pm_in)
        pm_out = merge_prefer_actual_over_duty_stamp(existing_pm_out, upload_pm_out, 'pm_out')
    am_out, pm_in = _resolve_dtr_am_out_pm_in_conflict(am_out, pm_in)
    return am_in, am_out, pm_in, pm_out


def _dtr_upload_force_processed_allowed(user=None) -> bool:
    """Only Admin may force re-upload into a processed quincena."""
    from app.portal_auth import is_portal_admin_session
    from app.portal_config import is_admin_role
    if is_portal_admin_session():
        return True
    user = user or current_user
    return is_admin_role(getattr(user, 'role', None))


def _dtr_upload_period_gate(upload_quincena: str, *, force_processed: bool = False) -> str | None:
    """Return error message if upload must be blocked for this quincena; else None."""
    qy, qm, qh = _parse_quincena_upload_value(upload_quincena)
    if qy is None or qm is None or qh is None:
        return None
    period = dtr_period.get_period(qy, qm, str(qh))
    if not dtr_period.is_period_locked(period):
        return None
    if force_processed and _dtr_upload_force_processed_allowed():
        return None
    return (
        'This quincena has been processed. Reopen it from Quincena Processing before uploading, '
        'or an Admin can check “Force upload into processed quincena”.'
    )


def _parse_dtr_special_days_for_generate(
    raw: str, start_d: date, end_d: date
) -> list[tuple[date, str | None]]:
    """
    Parse optional holiday / work-suspension list from Generate DTR.
    One token per line or comma/semicolon separated. Formats:
      YYYY-MM-DD | DD/MM/YYYY
      YYYY-MM-DD AM | YYYY-MM-DD PM  (half-day)
    Only dates within the generated period are kept; duplicate tokens are dropped.
    """
    if not (raw or '').strip():
        return []
    parts = re.split(r'[\n,;]+', raw.strip())
    out: list[tuple[date, str | None]] = []
    seen: set[tuple[date, str | None]] = set()
    for part in parts:
        part = part.strip()
        if not part:
            continue
        half = None
        m = re.match(r'^(.+?)\s+(AM|PM)$', part, re.IGNORECASE)
        if m:
            part = m.group(1).strip()
            half = m.group(2).upper()
        d = None
        for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
            try:
                d = datetime.strptime(part, fmt).date()
                break
            except ValueError:
                continue
        if d is None or d < start_d or d > end_d:
            continue
        key = (d, half)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    out.sort(key=lambda x: (x[0], x[1] or ''))
    return out


def _dtr_is_special_day_remark(remarks: str | None) -> bool:
    """True when stored remarks mark a holiday or work suspension (incl. AM/PM half-day)."""
    if not remarks:
        return False
    r = remarks.strip().upper()
    return r.startswith('HOLIDAY') or r.startswith('WORK SUSPENSION')


def _dtr_effective_remarks_for_day(emp: Employee, rec, curr: date, approved_dates: set[date]) -> str:
    """Remarks label used for DTR worktime aggregation (matches regeneration / records view)."""
    dow = curr.weekday()
    remarks = (
        _dtr_normalize_remarks(rec.remarks) if rec and rec.remarks
        else ('SATURDAY' if dow == 5 else ('SUNDAY' if dow == 6 else ''))
    )
    if curr in approved_dates and not remarks:
        remarks = 'APPROVED LEAVE'
    elif (
        not remarks
        and curr.weekday() == 4
        and _arrangement_model_code_for_date(emp, curr) == '4day'
        and not (rec and any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]))
        and not (rec and _dtr_is_special_day_remark(rec.remarks))
    ):
        remarks = 'REST DAY'
    else:
        flexi_sched = _flexible_worktime_duty_schedule_for_date(emp, curr)
        if flexi_sched and _duty_schedule_is_rest(flexi_sched):
            if not remarks or remarks in ('SATURDAY', 'SUNDAY'):
                remarks = 'REST DAY'
    return remarks


def _duty_schedule_from_flexible_worktime_row(row: FlexibleWorktime) -> dict:
    mode = ((getattr(row, 'duty_mode', None) or 'split')).strip().lower()
    if mode not in ('split', 'straight', 'rest'):
        mode = 'split'
    if mode == 'rest':
        return {
            'duty_mode': 'rest',
            'am_in': (0, 0),
            'am_out': (0, 0),
            'pm_in': (0, 0),
            'pm_out': (0, 0),
        }
    tin = row.time_in
    tout = row.time_out
    if tin is None or tout is None:
        raise ValueError('Flexible worktime split/straight rows require time in and time out.')
    if mode == 'straight':
        # Single continuous window; am_out/pm_in unused by straight helpers.
        return {
            'duty_mode': 'straight',
            'am_in': (tin.hour, tin.minute),
            'am_out': (tin.hour, tin.minute),
            'pm_in': (tout.hour, tout.minute),
            'pm_out': (tout.hour, tout.minute),
        }
    bout = row.break_out or tin
    bin_ = row.break_in or tout
    return {
        'duty_mode': 'split',
        'am_in': (tin.hour, tin.minute),
        'am_out': (bout.hour, bout.minute),
        'pm_in': (bin_.hour, bin_.minute),
        'pm_out': (tout.hour, tout.minute),
    }


def _employee_on_flexible_worktime_quincena(
    emp_id: int, year: int, month: int, quincena_half: str
) -> bool:
    return (
        FlexibleWorktime.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        ).first()
        is not None
    )


def _flexible_worktime_entry_date_bounds(
    start_d: date,
    end_d: date,
    *,
    buffer_days: int = DTR_UPLOAD_QUINCENA_BUFFER_DAYS,
) -> tuple[date, date]:
    """
    Selectable date-from / date-to window for Flexible Worktime entries.
    Widens the quincena by one day on each side so night-shift ranges can
    cover the adjacent calendar day (e.g. 8 pm → 5 am).
    """
    buf = max(0, int(buffer_days))
    return start_d - timedelta(days=buf), end_d + timedelta(days=buf)


def _flexible_worktime_schedule_by_date(
    emp_id: int, year: int, month: int, quincena_half: str, start_d: date, end_d: date
) -> dict[date, dict]:
    """Map each assigned calendar day (incl. weekends) to flexi duty schedules for regeneration."""
    rows = (
        FlexibleWorktime.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        )
        .order_by(FlexibleWorktime.date_start.asc(), FlexibleWorktime.id.asc())
        .all()
    )
    # Include ±1 day so overnight / night-shift entry ranges still map.
    win_start, win_end = _flexible_worktime_entry_date_bounds(start_d, end_d)
    sched_by_date: dict[date, dict] = {}
    for row in rows:
        sched = _duty_schedule_from_flexible_worktime_row(row)
        curr = max(row.date_start, win_start)
        stop = min(row.date_end, win_end)
        while curr <= stop:
            sched_by_date[curr] = sched
            curr += timedelta(days=1)
    return sched_by_date


def _dtr_standard_non_countable_day(emp: Employee, curr: date, remarks: str) -> bool:
    """
    Weekends and 4-day REST DAY Fridays are not counted during quincena regeneration
    for employees not on the flexible_worktime list (even when DTR has punches).
    """
    if curr.weekday() >= 5:
        return True
    if curr.weekday() == 4 and _arrangement_model_code_for_date(emp, curr) == '4day':
        if _dtr_is_special_day_remark(remarks):
            return False
        return True
    return False


def _parse_flexible_worktime_time(raw) -> time | None:
    s = (raw or '').strip()
    if not s:
        return None
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return _parse_flexi_display_to_time(s)


def _flexible_worktime_parse_filters(args) -> dict:
    today = date.today()
    try:
        year = int((args.get('year') or str(today.year)).strip())
    except ValueError:
        year = today.year
    try:
        month = int((args.get('month') or str(today.month)).strip())
    except ValueError:
        month = today.month
    quincena = (args.get('quincena') or ('1' if today.day <= 15 else '2')).strip()
    if quincena not in ('1', '2'):
        quincena = '1'
    if month < 1 or month > 12:
        month = today.month
    if year < 2000 or year > 2100:
        year = today.year
    dept_raw = (args.get('department_id') or '').strip()
    return {'year': year, 'month': month, 'quincena': quincena, 'department_id': dept_raw}


def _flexible_worktime_row_dict(row: FlexibleWorktime) -> dict:
    emp = row.employee
    code = (emp.employee_id or '') if emp else ''
    name = emp.directory_name if emp else ''
    dept = emp.department if emp else None
    mode = ((getattr(row, 'duty_mode', None) or 'split')).strip().lower()
    if mode not in ('split', 'straight', 'rest'):
        mode = 'split'
    sched = _duty_schedule_from_flexible_worktime_row(row)
    if mode == 'rest' or not row.time_in or not row.time_out:
        is_overnight = False
    else:
        t_in = row.time_in.hour * 60 + row.time_in.minute
        t_out = row.time_out.hour * 60 + row.time_out.minute
        is_overnight = t_out < t_in
    return {
        'id': row.id,
        'employee_id': row.employee_id,
        'employee_code': code,
        'employee_name': name,
        'department_id': emp.department_id if emp else None,
        'department_name': dept.name if dept else '',
        'date_start': row.date_start.isoformat(),
        'date_end': row.date_end.isoformat(),
        'duty_mode': mode,
        'time_in': row.time_in.strftime('%H:%M') if row.time_in else '',
        'break_out': row.break_out.strftime('%H:%M') if row.break_out else '',
        'break_in': row.break_in.strftime('%H:%M') if row.break_in else '',
        'time_out': row.time_out.strftime('%H:%M') if row.time_out else '',
        'is_overnight': is_overnight,
        'shift_mins': _scheduled_shift_minutes_from_duty_schedule(sched),
        'search_text': f"{code} {name} {dept.name if dept else ''}".lower(),
    }


def _duty_schedule_from_quincena_time_row(row) -> dict:
    return {
        'am_in': (row.time_in.hour, row.time_in.minute),
        'am_out': (row.break_out.hour, row.break_out.minute),
        'pm_in': (row.break_in.hour, row.break_in.minute),
        'pm_out': (row.time_out.hour, row.time_out.minute),
    }


def _employee_on_overtime_authorization_quincena(
    emp_id: int, year: int, month: int, quincena_half: str
) -> bool:
    return (
        OvertimeAuthorization.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        ).first()
        is not None
    )


def _overtime_authorization_schedule_by_date(
    emp_id: int, year: int, month: int, quincena_half: str, start_d: date, end_d: date
) -> dict[date, dict]:
    """Map each authorized calendar day (incl. weekends) to OT duty schedules."""
    rows = (
        OvertimeAuthorization.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        )
        .order_by(OvertimeAuthorization.date_start.asc(), OvertimeAuthorization.id.asc())
        .all()
    )
    sched_by_date: dict[date, dict] = {}
    for row in rows:
        sched = _duty_schedule_from_quincena_time_row(row)
        curr = max(row.date_start, start_d)
        stop = min(row.date_end, end_d)
        while curr <= stop:
            sched_by_date[curr] = sched
            curr += timedelta(days=1)
    return sched_by_date


def _overtime_authorization_cto_minutes_for_employee(
    emp_id: int, year: int, month: int, quincena_half: str, start_d: date, end_d: date
) -> int:
    """CTO minutes from OT authorization — only when DTR punches fall within scheduled windows."""
    emp = Employee.query.get(emp_id)
    if not emp:
        return 0
    rows = OvertimeAuthorization.query.filter_by(
        employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
    ).all()
    if not rows:
        return 0
    dtr_by_date = {r.record_date: r for r in _dtr_rows_for_employee(emp, start_d, end_d)}
    total = 0
    for row in rows:
        sched = _duty_schedule_from_quincena_time_row(row)
        curr = max(row.date_start, start_d)
        stop = min(row.date_end, end_d)
        while curr <= stop:
            rec = dtr_by_date.get(curr)
            if rec and any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]):
                total += _worked_minutes_within_flexi_schedule(rec, sched)
            curr += timedelta(days=1)
    return total


def _employee_on_jo_cos_extend_service_quincena(
    emp_id: int, year: int, month: int, quincena_half: str
) -> bool:
    return (
        JoCosExtendService.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        ).first()
        is not None
    )


def _jo_cos_extend_service_schedule_by_date(
    emp_id: int, year: int, month: int, quincena_half: str, start_d: date, end_d: date
) -> dict[date, dict]:
    """Map each authorized calendar day (incl. weekends) to extended-service duty schedules."""
    rows = (
        JoCosExtendService.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        )
        .order_by(JoCosExtendService.date_start.asc(), JoCosExtendService.id.asc())
        .all()
    )
    sched_by_date: dict[date, dict] = {}
    for row in rows:
        sched = _duty_schedule_from_quincena_time_row(row)
        curr = max(row.date_start, start_d)
        stop = min(row.date_end, end_d)
        while curr <= stop:
            sched_by_date[curr] = sched
            curr += timedelta(days=1)
    return sched_by_date


def _jo_cos_overtime_day_rows_for_employee(
    emp_id: int, year: int, month: int, quincena_half: str, start_d: date, end_d: date
) -> list[dict]:
    """Per-day JO/COS OT minutes — only when DTR punches fall within authorized schedule windows."""
    rows = JoCosExtendService.query.filter_by(
        employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
    ).all()
    if not rows:
        return []
    emp = Employee.query.get(emp_id)
    if not emp:
        return []
    dtr_by_date = {r.record_date: r for r in _dtr_rows_for_employee(emp, start_d, end_d)}
    by_date: dict[date, int] = {}
    for row in rows:
        sched = _duty_schedule_from_quincena_time_row(row)
        curr = max(row.date_start, start_d)
        stop = min(row.date_end, end_d)
        while curr <= stop:
            rec = dtr_by_date.get(curr)
            if rec and any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]):
                mins = _worked_minutes_within_flexi_schedule(rec, sched)
                if mins > 0:
                    by_date[curr] = by_date.get(curr, 0) + mins
            curr += timedelta(days=1)
    return [{'work_date': d, 'overtime_mins': m} for d, m in sorted(by_date.items())]


def _require_jo_cos_employee():
    """Return linked Employee for current user if JO/COS; else flash and redirect."""
    emp = _current_employee_for_user()
    if not emp or not _is_jo_cos_employee(emp):
        flash('Access denied. This section is for Job Order and Contract of Service employees only.', 'error')
        return None, redirect(url_for(_dashboard_for_user(current_user)))
    return emp, None


def _jo_cos_offset_hours_per_day(mode: str) -> Decimal:
    m = (mode or '').strip().upper()
    if m == 'FULL':
        return Decimal('8')
    if m in ('AM', 'PM'):
        return Decimal('4')
    return Decimal('0')


def _jo_cos_offset_total_hours(date_start: date, date_end: date, mode: str) -> Decimal:
    days = (date_end - date_start).days + 1
    if days < 1:
        return Decimal('0')
    return Decimal(str(days)) * _jo_cos_offset_hours_per_day(mode)


def _jo_cos_overtime_pending_offset_hours(emp_id: int) -> Decimal:
    rows = JoCosOvertimeOffsetRequest.query.filter_by(
        employee_id=emp_id, status='pending'
    ).all()
    return sum((Decimal(str(r.total_hours or 0)) for r in rows), Decimal('0'))


def _jo_cos_overtime_balance_hours(emp_id: int) -> Decimal:
    last = (
        JoCosOvertimeLedger.query.filter_by(employee_id=emp_id)
        .order_by(JoCosOvertimeLedger.id.desc())
        .first()
    )
    return Decimal(str(last.balance_hours)) if last else Decimal('0')


def _jo_cos_overtime_available_balance_hours(emp_id: int) -> Decimal:
    return _jo_cos_overtime_balance_hours(emp_id) - _jo_cos_overtime_pending_offset_hours(emp_id)


def _recompute_jo_cos_overtime_ledger_balances(emp_id: int) -> None:
    rows = (
        JoCosOvertimeLedger.query.filter_by(employee_id=emp_id)
        .order_by(JoCosOvertimeLedger.transaction_date.asc(), JoCosOvertimeLedger.id.asc())
        .all()
    )
    bal = Decimal('0')
    for row in rows:
        if row.entry_type == 'earned':
            bal += Decimal(str(row.hours_earned or 0))
        elif row.entry_type == 'offset':
            bal -= Decimal(str(row.offset_hours or 0))
        row.balance_hours = bal
    db.session.flush()


def _format_jo_cos_hours(val) -> str:
    try:
        h = Decimal(str(val or 0))
    except Exception:
        h = Decimal('0')
    return f'{h:.2f}'


def _mins_to_hours_decimal(mins: int) -> Decimal:
    return (Decimal(str(mins)) / Decimal('60')).quantize(Decimal('0.01'))


def _jo_cos_extend_service_row_for_date(
    emp_id: int, year: int, month: int, quincena_half: str, work_date: date
) -> JoCosExtendService | None:
    return (
        JoCosExtendService.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        )
        .filter(JoCosExtendService.date_start <= work_date)
        .filter(JoCosExtendService.date_end >= work_date)
        .order_by(JoCosExtendService.id.asc())
        .first()
    )


def _jo_cos_overtime_quincena_detail_lines(
    emp_id: int, year: int, month: int, quincena_half: str
) -> list[dict]:
    ot_rows = (
        JoCosOvertime.query.filter_by(
            employee_id=emp_id, year=year, month=month, quincena_half=quincena_half
        )
        .order_by(JoCosOvertime.work_date.asc())
        .all()
    )
    lines: list[dict] = []
    for ot in ot_rows:
        ext = _jo_cos_extend_service_row_for_date(emp_id, year, month, quincena_half, ot.work_date)
        lines.append({
            'work_date': ot.work_date.isoformat(),
            'time_in': ext.time_in.strftime('%H:%M') if ext and ext.time_in else '—',
            'break_out': ext.break_out.strftime('%H:%M') if ext and ext.break_out else '—',
            'break_in': ext.break_in.strftime('%H:%M') if ext and ext.break_in else '—',
            'time_out': ext.time_out.strftime('%H:%M') if ext and ext.time_out else '—',
            'total_hours': _format_jo_cos_hours(_mins_to_hours_decimal(ot.overtime_mins)),
        })
    return lines


def _jo_cos_overtime_ledger_display_rows(emp_id: int) -> list[dict]:
    rows = (
        JoCosOvertimeLedger.query.filter_by(employee_id=emp_id)
        .order_by(JoCosOvertimeLedger.transaction_date.desc(), JoCosOvertimeLedger.id.desc())
        .all()
    )
    out: list[dict] = []
    for r in rows:
        period = ''
        if r.entry_type == 'earned' and r.year and r.month and r.quincena_half:
            qlabel = '1st' if r.quincena_half == '1' else '2nd'
            period = f'{qlabel} quincena — {date(r.year, r.month, 1).strftime("%B %Y")}'
        elif r.entry_type == 'offset':
            if r.offset_date_start and r.offset_date_end:
                if r.offset_date_start == r.offset_date_end:
                    period = r.offset_date_start.isoformat()
                else:
                    period = f'{r.offset_date_start.isoformat()} to {r.offset_date_end.isoformat()}'
            period = f'Offset ({(r.offset_mode or "").upper()}) — {period}'
        detail_key = None
        detail_lines: list[dict] = []
        if r.entry_type == 'earned' and r.year and r.month and r.quincena_half:
            detail_key = f'{r.year}-{r.month:02d}-Q{r.quincena_half}'
            detail_lines = _jo_cos_overtime_quincena_detail_lines(
                emp_id, r.year, r.month, r.quincena_half
            )
        out.append({
            'id': r.id,
            'entry_type': r.entry_type,
            'period': period,
            'hours_earned': _format_jo_cos_hours(r.hours_earned) if r.entry_type == 'earned' else '—',
            'offset_date': (
                (
                    r.offset_date_start.isoformat()
                    if r.offset_date_start == r.offset_date_end
                    else f'{r.offset_date_start.isoformat()} — {r.offset_date_end.isoformat()}'
                )
                if r.entry_type == 'offset' and r.offset_date_start and r.offset_date_end
                else '—'
            ),
            'offset_hours': _format_jo_cos_hours(r.offset_hours) if r.entry_type == 'offset' else '—',
            'balance': _format_jo_cos_hours(r.balance_hours),
            'detail_key': detail_key,
            'detail_lines': detail_lines,
            'particulars': r.particulars or '',
        })
    return out


def _create_hrms_notification(
    user_id: int,
    title: str,
    message: str,
    *,
    link_url: str | None = None,
    related_type: str | None = None,
    related_id: int | None = None,
) -> None:
    if not user_id:
        return
    db.session.add(HrmsNotification(
        user_id=user_id,
        title=title,
        message=message,
        link_url=link_url,
        related_type=related_type,
        related_id=related_id,
        is_read=False,
    ))


def _notify_jo_cos_offset_submitted(req: JoCosOvertimeOffsetRequest) -> None:
    emp = req.employee
    if not emp:
        return
    name = emp.full_name
    link = url_for('routes.jo_cos_overtime_offset_approvals')
    title = 'JO/COS overtime offset pending approval'
    message = (
        f'{name} applied for {_format_jo_cos_hours(req.total_hours)} hour(s) overtime offset '
        f'({req.date_start.isoformat()} to {req.date_end.isoformat()}, {(req.offset_mode or "").upper()}).'
    )
    notified: set[int] = set()
    if emp.department and emp.department.manager_id:
        mgr = Employee.query.get(emp.department.manager_id)
        if mgr and mgr.user_id and mgr.user_id not in notified:
            _create_hrms_notification(
                mgr.user_id, title, message, link_url=link,
                related_type='jo_cos_overtime_offset', related_id=req.id,
            )
            notified.add(mgr.user_id)
    for u in User.query.filter(User.role.in_(['hr', 'admin'])).all():
        if u.id not in notified:
            _create_hrms_notification(
                u.id, title, message, link_url=link,
                related_type='jo_cos_overtime_offset', related_id=req.id,
            )
            notified.add(u.id)


def _can_approve_jo_cos_overtime_offset(req: JoCosOvertimeOffsetRequest) -> bool:
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role in ('hr', 'admin'):
        return True
    if role == 'manager':
        emp = req.employee
        dept_ids = _managed_department_ids_for_manager()
        return bool(emp and emp.department_id in dept_ids)
    return False


def _backfill_jo_cos_overtime_ledger_for_employee(emp_id: int) -> None:
    """Create missing earned ledger rows from existing jo_cos_overtime snapshots."""
    groups = (
        db.session.query(
            JoCosOvertime.year,
            JoCosOvertime.month,
            JoCosOvertime.quincena_half,
            func.coalesce(func.sum(JoCosOvertime.overtime_mins), 0),
        )
        .filter_by(employee_id=emp_id)
        .group_by(JoCosOvertime.year, JoCosOvertime.month, JoCosOvertime.quincena_half)
        .all()
    )
    changed = False
    for year, month, quincena, _total in groups:
        tag = _dtr_regen_tag(year, month, quincena)
        if JoCosOvertimeLedger.query.filter_by(
            employee_id=emp_id, entry_type='earned', regen_tag=tag
        ).first():
            continue
        _, end_d = _quincena_date_range(year, month, quincena)
        _sync_jo_cos_overtime_earned_ledger_for_quincena(
            emp_id, year, month, quincena, end_d, tag, None,
        )
        changed = True
    if changed:
        db.session.commit()


def _sync_jo_cos_overtime_earned_ledger_for_quincena(
    emp_id: int,
    year: int,
    month: int,
    quincena: str,
    end_d: date,
    tag: str,
    created_by_user_id: int | None,
) -> None:
    JoCosOvertimeLedger.query.filter_by(
        employee_id=emp_id, entry_type='earned', regen_tag=tag
    ).delete(synchronize_session=False)
    total_mins = (
        db.session.query(func.coalesce(func.sum(JoCosOvertime.overtime_mins), 0))
        .filter_by(employee_id=emp_id, year=year, month=month, quincena_half=quincena)
        .scalar()
    )
    total_mins = int(total_mins or 0)
    if total_mins <= 0:
        _recompute_jo_cos_overtime_ledger_balances(emp_id)
        return
    hours = _mins_to_hours_decimal(total_mins)
    db.session.add(JoCosOvertimeLedger(
        employee_id=emp_id,
        entry_type='earned',
        transaction_date=end_d,
        year=year,
        month=month,
        quincena_half=quincena,
        hours_earned=hours,
        balance_hours=Decimal('0'),
        regen_tag=tag,
        particulars=f'DTR quincena regen OT earned — {year:04d}-{month:02d} Q{quincena}',
        created_by_user_id=created_by_user_id,
    ))
    db.session.flush()
    _recompute_jo_cos_overtime_ledger_balances(emp_id)


def _jo_cos_extend_service_row_dict(row: JoCosExtendService) -> dict:
    emp = row.employee
    code = (emp.employee_id or '') if emp else ''
    name = emp.directory_name if emp else ''
    return {
        'id': row.id,
        'employee_id': row.employee_id,
        'employee_code': code,
        'employee_name': name,
        'date_start': row.date_start.isoformat(),
        'date_end': row.date_end.isoformat(),
        'time_in': row.time_in.strftime('%H:%M'),
        'break_out': row.break_out.strftime('%H:%M'),
        'break_in': row.break_in.strftime('%H:%M'),
        'time_out': row.time_out.strftime('%H:%M'),
        'search_text': f"{code} {name}".lower(),
    }


def _overtime_authorization_row_dict(row: OvertimeAuthorization) -> dict:
    emp = row.employee
    code = (emp.employee_id or '') if emp else ''
    name = emp.directory_name if emp else ''
    return {
        'id': row.id,
        'employee_id': row.employee_id,
        'employee_code': code,
        'employee_name': name,
        'date_start': row.date_start.isoformat(),
        'date_end': row.date_end.isoformat(),
        'time_in': row.time_in.strftime('%H:%M'),
        'break_out': row.break_out.strftime('%H:%M'),
        'break_in': row.break_in.strftime('%H:%M'),
        'time_out': row.time_out.strftime('%H:%M'),
        'search_text': f"{code} {name}".lower(),
    }


def _parse_quincena_schedule_entries_json(
    raw: str,
    start_d: date,
    end_d: date,
    *,
    require_plantilla: bool = False,
    require_jo_cos: bool = False,
    allowed_department_id: int | None = None,
) -> list[dict]:
    raw = (raw or '').strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError('Invalid schedule list JSON.') from e
    if not isinstance(data, list):
        raise ValueError('Schedule list must be a JSON array.')
    parsed: list[dict] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'Row {idx}: invalid entry.')
        try:
            emp_id = int(item.get('employee_id'))
        except (TypeError, ValueError) as e:
            raise ValueError(f'Row {idx}: select an employee.') from e
        emp = Employee.query.get(emp_id)
        if not emp or (emp.status or '').strip().lower() != 'active':
            raise ValueError(f'Row {idx}: employee not found or not active.')
        if require_plantilla and not _is_plantilla_leave_credits_employee(emp):
            raise ValueError(f'Row {idx}: employee must be plantilla (leave-credits status).')
        if require_jo_cos and not _is_jo_cos_employee(emp):
            raise ValueError(f'Row {idx}: employee must be Job Order or Contract of Service.')
        if allowed_department_id is not None and emp.department_id != allowed_department_id:
            raise ValueError(
                f'Row {idx}: employee is outside your department. '
                'Payroll Maker may only manage X-Service for their department.'
            )
        try:
            d0 = datetime.strptime((item.get('date_start') or '').strip(), '%Y-%m-%d').date()
            d1 = datetime.strptime((item.get('date_end') or '').strip(), '%Y-%m-%d').date()
        except ValueError as e:
            raise ValueError(f'Row {idx}: invalid date range.') from e
        if d0 > d1:
            raise ValueError(f'Row {idx}: start date must be on or before end date.')
        if d0 < start_d or d1 > end_d:
            raise ValueError(f'Row {idx}: dates must fall within the selected quincena.')
        times = {}
        for key in ('time_in', 'break_out', 'break_in', 'time_out'):
            t = _parse_flexible_worktime_time(item.get(key))
            if not t:
                raise ValueError(f'Row {idx}: invalid {key.replace("_", " ")}.')
            times[key] = t
        parsed.append({
            'employee_id': emp_id,
            'date_start': d0,
            'date_end': d1,
            **times,
        })
    by_emp: dict[int, list[tuple[date, date]]] = {}
    for row in parsed:
        ranges = by_emp.setdefault(row['employee_id'], [])
        for a0, a1 in ranges:
            if row['date_start'] <= a1 and a0 <= row['date_end']:
                raise ValueError('Overlapping date ranges for the same employee are not allowed.')
        ranges.append((row['date_start'], row['date_end']))
    return parsed


def _employee_dtr_worktime_day_rows(
    emp: Employee,
    start_d: date,
    end_d: date,
    *,
    persist_row_undertime: bool = False,
    quincena_year: int | None = None,
    quincena_month: int | None = None,
    quincena_half: str | None = None,
) -> list[dict]:
    """Per-calendar-day worktime breakdown for one employee over a date range."""
    _prefetch_flexi_schedule_rows_for_range(emp, start_d, end_d)
    rows = _dtr_rows_for_employee(emp, start_d, end_d)
    by_date = {r.record_date: r for r in rows}
    approved = _approved_leave_dates_for_employee(emp.id, start_d, end_d)
    jo_cos = _is_jo_cos_employee(emp)
    flexi_context = (
        quincena_year is not None and quincena_month is not None and quincena_half in ('1', '2')
    )
    on_flexi_list = False
    flexi_by_date: dict[date, dict] = {}
    on_extend_list = False
    extend_by_date: dict[date, dict] = {}
    if flexi_context:
        on_flexi_list = _employee_on_flexible_worktime_quincena(
            emp.id, quincena_year, quincena_month, quincena_half
        )
        if on_flexi_list:
            flexi_by_date = _flexible_worktime_schedule_by_date(
                emp.id, quincena_year, quincena_month, quincena_half, start_d, end_d
            )
        if jo_cos:
            on_extend_list = _employee_on_jo_cos_extend_service_quincena(
                emp.id, quincena_year, quincena_month, quincena_half
            )
            if on_extend_list:
                extend_by_date = _jo_cos_extend_service_schedule_by_date(
                    emp.id, quincena_year, quincena_month, quincena_half, start_d, end_d
                )

    day_rows: list[dict] = []
    curr = start_d
    while curr <= end_d:
        rec = by_date.get(curr)
        is_weekend = curr.weekday() >= 5
        remarks = _dtr_effective_remarks_for_day(emp, rec, curr, approved)
        flexi_sched = flexi_by_date.get(curr) if flexi_context else None
        extend_sched = extend_by_date.get(curr) if flexi_context and jo_cos else None

        if flexi_context:
            if on_flexi_list and not flexi_sched:
                day_rows.append({
                    'work_date': curr,
                    'remarks': (remarks or '')[:100] or None,
                    'gross_work_mins': 0,
                    'late_mins': 0,
                    'undertime_mins': 0,
                    'absence_mins': 0,
                    'net_rendered_mins': 0,
                })
                curr += timedelta(days=1)
                continue
            if flexi_sched and _duty_schedule_is_rest(flexi_sched):
                # Explicit flexi rest day: not duty; keep leave/holiday labels if present.
                if _dtr_is_special_day_remark(remarks) or _dtr_is_paid_leave_remark(remarks):
                    rest_remarks = remarks
                else:
                    rest_remarks = 'REST DAY'
                if persist_row_undertime and rec:
                    rec.undertime_hrs = 0
                    rec.undertime_mins = 0
                day_rows.append({
                    'work_date': curr,
                    'remarks': (rest_remarks or '')[:100] or None,
                    'gross_work_mins': 0,
                    'late_mins': 0,
                    'undertime_mins': 0,
                    'absence_mins': 0,
                    'net_rendered_mins': 0,
                })
                curr += timedelta(days=1)
                continue
            if jo_cos and on_extend_list and extend_sched:
                day_rows.append({
                    'work_date': curr,
                    'remarks': (remarks or '')[:100] or None,
                    'gross_work_mins': 0,
                    'late_mins': 0,
                    'undertime_mins': 0,
                    'absence_mins': 0,
                    'net_rendered_mins': 0,
                })
                curr += timedelta(days=1)
                continue
            if not on_flexi_list and _dtr_standard_non_countable_day(emp, curr, remarks):
                day_rows.append({
                    'work_date': curr,
                    'remarks': (remarks or '')[:100] or None,
                    'gross_work_mins': 0,
                    'late_mins': 0,
                    'undertime_mins': 0,
                    'absence_mins': 0,
                    'net_rendered_mins': 0,
                })
                curr += timedelta(days=1)
                continue

        schedule = None
        day_late = day_ut = day_absence = 0
        compute_weekday = flexi_sched is not None or not is_weekend
        if flexi_sched:
            schedule = flexi_sched
        elif compute_weekday:
            schedule = _pick_late_undertime_schedule(emp, curr)
        if compute_weekday and schedule:
            if rec:
                late_m, ut_m = _late_undertime_minutes_for_record(rec, schedule)
                day_late = late_m
                day_ut = ut_m
                if persist_row_undertime:
                    total_lut = late_m + ut_m
                    rec.undertime_hrs = total_lut // 60
                    rec.undertime_mins = total_lut % 60
            has_punch = rec and any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out])
            if not has_punch and not remarks:
                day_absence = _scheduled_shift_minutes_from_duty_schedule(schedule)
        if flexi_sched:
            day_gross = _dtr_gross_minutes_for_flexi_day(rec, schedule, remarks)
        else:
            day_gross = _dtr_gross_minutes_for_day(
                rec, schedule, remarks, is_weekend and not flexi_sched, emp=emp, d=curr
            )
        if jo_cos:
            day_net = max(0, day_gross - day_late - day_ut - day_absence)
        else:
            day_net = day_gross
        day_rows.append({
            'work_date': curr,
            'remarks': (remarks or '')[:100] or None,
            'gross_work_mins': day_gross,
            'late_mins': day_late,
            'undertime_mins': day_ut,
            'absence_mins': day_absence,
            'net_rendered_mins': day_net,
        })
        curr += timedelta(days=1)
    _dtr_rebalance_fourday_rest_friday_holiday_credits(day_rows, emp)
    return day_rows


def _aggregate_employee_dtr_worktime(
    emp: Employee,
    start_d: date,
    end_d: date,
    *,
    persist_row_undertime: bool = False,
    quincena_year: int | None = None,
    quincena_month: int | None = None,
    quincena_half: str | None = None,
) -> dict:
    """Gross/late/undertime/absence/net totals for one employee over a date range."""
    day_rows = _employee_dtr_worktime_day_rows(
        emp,
        start_d,
        end_d,
        persist_row_undertime=persist_row_undertime,
        quincena_year=quincena_year,
        quincena_month=quincena_month,
        quincena_half=quincena_half,
    )
    gross = sum(r['gross_work_mins'] for r in day_rows)
    late_tot = sum(r['late_mins'] for r in day_rows)
    ut_tot = sum(r['undertime_mins'] for r in day_rows)
    absence_mins = sum(r['absence_mins'] for r in day_rows)
    absence_days = sum(1 for r in day_rows if r['absence_mins'] > 0)
    jo_cos = _is_jo_cos_employee(emp)
    if jo_cos:
        net = max(0, gross - late_tot - ut_tot - absence_mins)
    else:
        net = gross
    return {
        'gross': gross,
        'late': late_tot,
        'undertime': ut_tot,
        'absence_mins': absence_mins,
        'absence_days': absence_days,
        'net': net,
        'jo_cos': jo_cos,
        'day_rows': day_rows,
    }


def _dtr_special_dates_from_entries(
    *entry_lists: list[tuple[date, str | None]],
) -> set[date]:
    out: set[date] = set()
    for entries in entry_lists:
        for d, _half in entries:
            out.add(d)
    return out


def _dtr_skip_arrangement_stamp_for_row(rec, d: date, special_dates: set[date]) -> bool:
    """Do not apply REST DAY or default duty times on holidays / work suspensions."""
    if d in special_dates:
        return True
    if rec and _dtr_is_special_day_remark(rec.remarks):
        return True
    return False


def _dtr_get_or_create_row_for_special_day(emp: Employee, d: date):
    """Return DTR row for holiday/suspension stamping; create if missing."""
    rec = _dtr_find_row_by_employee_day_readonly(emp, d)
    if rec:
        return rec, False
    rec = DailyTimeRecord(employee_id=emp.id, record_date=d)
    db.session.add(rec)
    return rec, True


def _apply_dtr_special_day(rec, remark: str, half: str | None) -> None:
    """Mark a DTR row as holiday or work suspension (full day or AM/PM half)."""
    if half == 'AM':
        rec.am_in = rec.am_out = None
        rec.remarks = f'{remark} AM'
    elif half == 'PM':
        rec.pm_in = rec.pm_out = None
        rec.remarks = f'{remark} PM'
    else:
        rec.am_in = rec.am_out = rec.pm_in = rec.pm_out = None
        rec.remarks = remark
        rec.undertime_hrs = 0
        rec.undertime_mins = 0


def _parse_dtr_upload_form_date(raw: str):
    """Parse date from DTR upload form (YYYY-MM-DD or DD/MM/YYYY)."""
    raw = (raw or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _dtr_find_row_by_employee_day_readonly(emp: Employee, rec_date: date):
    """Return existing DTR row for this calendar day if any (by employees.id or legacy badge-as-FK). Does not mutate."""
    row = DailyTimeRecord.query.filter_by(employee_id=emp.id, record_date=rec_date).first()
    if row:
        return row
    badge = (emp.employee_id or '').strip()
    if badge.isdigit():
        bid = int(badge)
        if bid != emp.id:
            return DailyTimeRecord.query.filter_by(employee_id=bid, record_date=rec_date).first()
    return None


def _dtr_for_upload_employee_day(emp: Employee, rec_date: date):
    """
    Existing DailyTimeRecord for canonical employee pk and day.
    If a legacy row used numeric badge as daily_time_record.employee_id, repoint to emp.id.
    """
    row = DailyTimeRecord.query.filter_by(employee_id=emp.id, record_date=rec_date).first()
    if row:
        return row
    badge = (emp.employee_id or '').strip()
    if badge.isdigit():
        bid = int(badge)
        if bid != emp.id:
            legacy = DailyTimeRecord.query.filter_by(employee_id=bid, record_date=rec_date).first()
            if legacy:
                legacy.employee_id = emp.id
                return legacy
    return None


def _dtr_save_payload_from_upload_rows(parsed_rows):
    """
    Minimal serializable rows for compact POST (JSON in one field).
    Raw ``punches`` are carried through so duplicate-slot flags can be created on commit.
    """
    return [
        {
            'employee_id': str(r.get('employee_id') or '').strip(),
            'date': str(r.get('date') or '').strip(),
            'check_in': str(r.get('check_in') or '').strip(),
            'break_out': str(r.get('break_out') or '').strip(),
            'break_in': str(r.get('break_in') or '').strip(),
            'check_out': str(r.get('check_out') or '').strip(),
            'punches': r.get('punches') or [],
            'duplicate_slots': r.get('duplicate_slots') or [],
            'has_duplicate_punches': bool(r.get('has_duplicate_punches')),
        }
        for r in parsed_rows
    ]


def _dtr_upload_owner_key():
    """Stable per-session key so staged uploads cannot be read by other users."""
    return str(getattr(current_user, 'get_id', lambda: None)() or getattr(current_user, 'id', None) or 'anon')


def _dtr_upload_parse_summary(parsed_rows):
    """Aggregate stats for the post-parse confirmation panel (no per-row HTML)."""
    employee_ids = set()
    dates = []
    for row in parsed_rows:
        eid = str(row.get('employee_id') or '').strip()
        if eid:
            employee_ids.add(eid)
        raw = str(row.get('date') or '').strip()
        if not raw:
            continue
        try:
            dates.append(datetime.strptime(raw, '%Y-%m-%d').date())
        except ValueError:
            continue
    return {
        'record_count': len(parsed_rows),
        'employee_count': len(employee_ids),
        'date_min': min(dates).strftime('%d/%m/%Y') if dates else None,
        'date_max': max(dates).strftime('%d/%m/%Y') if dates else None,
    }


def _dtr_rows_from_payload_list(rows_raw):
    """Normalize decoded JSON list of row dicts. Returns (rows, error_message)."""
    if rows_raw is None:
        return [], 'Missing rows.'
    if not isinstance(rows_raw, list):
        return [], 'Invalid rows (expected a list).'
    rows = []
    for item in rows_raw:
        if not isinstance(item, dict):
            continue
        punches = item.get('punches')
        if not isinstance(punches, list):
            punches = []
        row = {
            'employee_id': str(item.get('employee_id') or '').strip(),
            'date': str(item.get('date') or '').strip(),
            'check_in': str(item.get('check_in') or '').strip(),
            'break_out': str(item.get('break_out') or '').strip(),
            'break_in': str(item.get('break_in') or '').strip(),
            'check_out': str(item.get('check_out') or '').strip(),
            'punches': punches,
        }
        dup_slots = item.get('duplicate_slots')
        if isinstance(dup_slots, list):
            row['duplicate_slots'] = dup_slots
            row['has_duplicate_punches'] = bool(dup_slots)
        rows.append(row)
    return rows, None


def _dtr_upload_save_parse_form_rows(req):
    """Return (row_dicts, error_message). Prefer JSON payload over legacy row_* keys."""
    json_raw = (req.form.get('dtr_rows_json') or '').strip()
    if json_raw:
        try:
            payload = json.loads(json_raw)
        except json.JSONDecodeError:
            return [], 'Invalid DTR rows payload (JSON).'
        return _dtr_rows_from_payload_list(payload)
    row_data = []
    for key in req.form:
        m = re.match(r'row_(\d+)_(employee_id|date|check_in|break_out|break_in|check_out)', key)
        if m:
            idx, field = m.group(1), m.group(2)
            while len(row_data) <= int(idx):
                row_data.append({})
            row_data[int(idx)][field] = req.form.get(key, '').strip()
    return row_data, None


def _dtr_rows_for_employee(emp: Employee, start_d: date, end_d: date):
    """Load DailyTimeRecord rows for date range. Uses employees.id; optional legacy badge-as-int."""
    q = (DailyTimeRecord.query
         .filter(DailyTimeRecord.employee_id == emp.id)
         .filter(DailyTimeRecord.record_date >= start_d)
         .filter(DailyTimeRecord.record_date <= end_d)
         .order_by(DailyTimeRecord.record_date.asc()))
    rows = q.all()
    if rows:
        return rows
    badge = (emp.employee_id or '').strip()
    if badge.isdigit():
        bid = int(badge)
        if bid != emp.id:
            leg = (DailyTimeRecord.query
                   .filter(DailyTimeRecord.employee_id == bid)
                   .filter(DailyTimeRecord.record_date >= start_d)
                   .filter(DailyTimeRecord.record_date <= end_d)
                   .order_by(DailyTimeRecord.record_date.asc())
                   .all())
            if leg:
                return leg
    return rows


def _employee_directory_label(last_name, first_name, middle_name, name_extension=None) -> str:
    last = (last_name or '').strip()
    first = (first_name or '').strip()
    middle = (middle_name or '').strip()
    ext = Employee.normalize_name_extension(name_extension)
    given_parts = []
    if first:
        given_parts.append(first)
    if ext:
        given_parts.append(ext)
    if middle:
        given_parts.append(middle)
    given = ' '.join(given_parts)
    if last and given:
        return f'{last}, {given}'
    return last or given


def _dtr_employee_picker_rows(employee_q) -> list:
    """
    Lightweight employee dropdown rows (no full Employee/PDS ORM load).

    directory_name on Employee lazy-loads employee_pds per option; PDS is a very
    wide table, so that N+1 dominated Load DTR for Admin/HR.
    """
    q = (
        employee_q
        .outerjoin(EmployeePDS, EmployeePDS.employee_id == Employee.id)
        .with_entities(
            Employee.id,
            Employee.employee_id,
            Employee.last_name,
            Employee.first_name,
            Employee.middle_name,
            EmployeePDS.name_extension,
        )
        .order_by(Employee.last_name.asc(), Employee.first_name.asc())
    )
    out = []
    for eid, badge, last, first, middle, ext in q.all():
        out.append(SimpleNamespace(
            id=eid,
            employee_id=badge,
            directory_name=_employee_directory_label(last, first, middle, ext),
        ))
    return out


def _resolve_employee_from_scoped_list(employees, selected_raw):
    if not employees:
        return None
    if not selected_raw:
        return employees[0]
    s = selected_raw.strip()
    try:
        pk = int(s)
    except ValueError:
        pk = None
    if pk is not None:
        for e in employees:
            if e.id == pk:
                return e
    for e in employees:
        if str(e.employee_id or '').strip() == s:
            return e
    return employees[0]


_DTR_UPLOAD_ALLOWED_ROLES = frozenset({'admin', 'hr', 'dtr_uploader'})
_DTR_PUNCH_FLAG_REVIEW_ROLES = frozenset({'admin', 'hr', 'payroll_maker'})
_FLEXIBLE_WORKTIME_ROLES = frozenset({'admin', 'hr', 'payroll_maker'})


def _user_is_admin_or_hr(user=None):
    from app.portal_auth import is_portal_admin_session
    from app.portal_config import is_admin_or_hr_role
    if is_portal_admin_session():
        return True
    user = user or current_user
    return is_admin_or_hr_role(getattr(user, 'role', None))


def _user_can_manage_flexible_worktime(user=None):
    """Admin/HR (and Portal Admin) or Payroll Maker may manage Flexible Worktime."""
    from app.portal_auth import is_portal_admin_session
    if is_portal_admin_session():
        return True
    role = (getattr(user or current_user, 'role', None) or '').strip().lower()
    return role in _FLEXIBLE_WORKTIME_ROLES


def _require_flexible_worktime_manager():
    """If current user cannot manage Flexible Worktime, flash and return redirect; else None."""
    if _user_can_manage_flexible_worktime():
        return None
    flash('Access denied. This section is for Admin, HR, and Payroll Maker.', 'error')
    return redirect(url_for(_dashboard_for_user(current_user)))


def _require_x_service_manager():
    """X-Service uses the same Admin/HR/Payroll Maker role policy."""
    if _user_can_manage_flexible_worktime():
        return None
    flash('Access denied. This section is for Admin, HR, and Payroll Maker.', 'error')
    return redirect(url_for(_dashboard_for_user(current_user)))


def _user_can_review_dtr_punch_flags(user=None):
    """Admin/HR (and Portal Admin) or Payroll Maker may review duplicate punch flags."""
    from app.portal_auth import is_portal_admin_session
    if is_portal_admin_session():
        return True
    role = (getattr(user or current_user, 'role', None) or '').strip().lower()
    return role in _DTR_PUNCH_FLAG_REVIEW_ROLES


def _require_dtr_punch_flag_reviewer():
    """If current user cannot review punch flags, flash and return redirect; else None."""
    if _user_can_review_dtr_punch_flags():
        return None
    flash('Access denied. This section is for Admin, HR, and Payroll Maker.', 'error')
    return redirect(url_for(_dashboard_for_user(current_user)))


def _payroll_maker_department_id(user=None):
    """Return the linked department id for a payroll_maker user, else None."""
    user = user or current_user
    role = (getattr(user, 'role', None) or '').strip().lower()
    if role != 'payroll_maker':
        return None
    viewer_emp = _current_employee_for_user()
    return getattr(viewer_emp, 'department_id', None) if viewer_emp else None


def _user_can_access_punch_flag_employee(emp, user=None) -> bool:
    """Admin/HR: any employee. Payroll Maker: same department only."""
    if emp is None:
        return False
    if _user_is_admin_or_hr(user):
        return True
    role = (getattr(user or current_user, 'role', None) or '').strip().lower()
    if role != 'payroll_maker':
        return False
    dept_id = _payroll_maker_department_id(user)
    return bool(dept_id and emp.department_id == dept_id)


def _user_can_upload_dtr_log(user=None):
    role = (getattr(user or current_user, 'role', None) or '').strip().lower()
    return role in _DTR_UPLOAD_ALLOWED_ROLES


def _dtr_upload_commit_rows(
    row_data,
    upload_quincena,
    *,
    filename='',
    record_count=0,
    merge_strategy=DTR_UPLOAD_MERGE_FILL_BLANKS,
    force_processed=False,
):
    """
    Persist parsed upload rows to daily_time_record with batch audit log.
    Also retains raw punches and opens duplicate-slot flags for HR review.
    Returns dict: saved, errors, error_count, batch_id, created_count, merged_count,
    unchanged_count, merge_strategy, punch_flag_count.
    Raises on DB failure (caller should rollback).
    """
    merge_strategy = _normalize_dtr_upload_merge_strategy(merge_strategy)
    batch = create_upload_batch(
        quincena=upload_quincena,
        filename=filename,
        record_count=record_count or len(row_data),
    )
    seen_normalized = set()
    saved = 0
    created_count = 0
    merged_count = 0
    unchanged_count = 0
    punch_flag_count = 0
    errors = []

    def parse_t(s):
        if not s:
            return None
        s = s.strip()
        for fmt in ('%H:%M', '%H:%M:%S'):
            try:
                return datetime.strptime(s, fmt).time()
            except ValueError:
                continue
        return None

    for r in row_data:
        eid_raw = (r.get('employee_id') or '').strip()
        date_raw = (r.get('date') or '').strip()
        if not eid_raw or not date_raw:
            continue
        emp = Employee.query.filter_by(employee_id=eid_raw).first()
        if not emp:
            errors.append(f'Employee ID {eid_raw} not found.')
            continue
        rec_date = _parse_dtr_upload_form_date(date_raw)
        if rec_date is None:
            errors.append(f'Invalid date {date_raw!r} for employee {eid_raw}.')
            continue
        norm_key = (emp.id, rec_date)
        if norm_key in seen_normalized:
            continue
        seen_normalized.add(norm_key)

        am_in_u = parse_t(r.get('check_in'))
        am_out_u = parse_t(r.get('break_out'))
        pm_in_u = parse_t(r.get('break_in'))
        pm_out_u = parse_t(r.get('check_out'))
        dtr = _dtr_for_upload_employee_day(emp, rec_date)
        if dtr:
            am_in, am_out, pm_in, pm_out = _merge_dtr_times_with_upload(
                dtr.am_in,
                dtr.am_out,
                dtr.pm_in,
                dtr.pm_out,
                am_in_u,
                am_out_u,
                pm_in_u,
                pm_out_u,
                strategy=merge_strategy,
            )
            if (
                am_in == dtr.am_in
                and am_out == dtr.am_out
                and pm_in == dtr.pm_in
                and pm_out == dtr.pm_out
            ):
                unchanged_count += 1
            else:
                before = snapshot_dtr_row(dtr)
                dtr.am_in = am_in
                dtr.am_out = am_out
                dtr.pm_in = pm_in
                dtr.pm_out = pm_out
                log_batch_item(
                    batch,
                    employee_id=emp.id,
                    record_date=rec_date,
                    action='merged',
                    dtr_record_id=dtr.id,
                    before=before,
                )
                merged_count += 1
            selected_times = {
                'am_in': am_in_u,
                'am_out': am_out_u,
                'pm_in': pm_in_u,
                'pm_out': pm_out_u,
            }
        else:
            am_out_u, pm_in_u = _resolve_dtr_am_out_pm_in_conflict(am_out_u, pm_in_u)
            dtr = DailyTimeRecord(
                employee_id=emp.id,
                record_date=rec_date,
                am_in=am_in_u,
                am_out=am_out_u,
                pm_in=pm_in_u,
                pm_out=pm_out_u,
            )
            db.session.add(dtr)
            db.session.flush()
            log_batch_item(
                batch,
                employee_id=emp.id,
                record_date=rec_date,
                action='created',
                dtr_record_id=dtr.id,
            )
            created_count += 1
            selected_times = {
                'am_in': am_in_u,
                'am_out': am_out_u,
                'pm_in': pm_in_u,
                'pm_out': pm_out_u,
            }
        punch_flag_count += persist_upload_punches_and_flags(
            batch,
            employee_id=emp.id,
            badge_employee_id=eid_raw,
            record_date=rec_date,
            punches=r.get('punches') or [],
            selected_times=selected_times,
        )
        saved += 1

    batch.saved_count = saved
    batch.error_count = len(errors)
    batch.created_count = created_count
    batch.merged_count = merged_count

    try:
        qy, qm, qh = _parse_quincena_upload_value(upload_quincena)
        period_label = upload_quincena
        if qy is not None and qm is not None and qh is not None:
            period_label = _jo_cos_payslip_period_label(qy, qm, str(qh))
        changed = created_count + merged_count
        msg = (
            f'DTR upload processed {saved} record(s) for {period_label} '
            f'({created_count} new, {merged_count} updated, {unchanged_count} unchanged).'
        )
        if punch_flag_count:
            msg += f' {punch_flag_count} duplicate punch flag(s) need review.'
        if errors:
            msg += f' {len(errors)} row(s) had errors.'
        _create_hrms_notification(
            getattr(current_user, 'id', None),
            'DTR upload completed',
            msg,
            link_url=url_for(
                'routes.dtr_upload_punch_flags',
                batch_id=batch.id,
            ) if punch_flag_count else url_for(
                'routes.dtr_records',
                mode='quincena',
                year=qy or date.today().year,
                month=qm or date.today().month,
                quincena=str(qh or '1'),
            ),
            related_type='dtr_upload',
            related_id=batch.id,
        )
        if qy is not None and qm is not None and qh is not None and changed > 0:
            period = dtr_period.get_period(qy, qm, str(qh))
            status = ((period.status if period else '') or '').strip() or dtr_period.STATUS_DRAFT
            was_locked = dtr_period.is_period_locked(period)
            if was_locked or status == dtr_period.STATUS_DRAFT or period is None:
                dtr_period.mark_uploaded(qy, qm, str(qh), getattr(current_user, 'id', None))
                dtr_period.notify_employees_dtr_review_open(qy, qm, str(qh))
            elif status == dtr_period.STATUS_REVIEW_CLOSED:
                dtr_period.open_review(period)
    except Exception:
        pass

    db.session.commit()
    return {
        'saved': saved,
        'errors': errors,
        'error_count': len(errors),
        'batch_id': batch.id,
        'created_count': created_count,
        'merged_count': merged_count,
        'unchanged_count': unchanged_count,
        'merge_strategy': merge_strategy,
        'force_processed': bool(force_processed),
        'punch_flag_count': punch_flag_count,
    }


@bp.route('/dtr-upload', methods=['GET', 'POST'])
@login_required
def dtr_upload():
    if not _user_can_upload_dtr_log():
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    employee = current_user.employee if current_user.employee else None
    save_result = None
    today = date.today()
    quincena_options = _build_quincena_upload_options(today)
    default_quincena = f'{today.year:04d}-{today.month:02d}-{"1" if today.day <= 15 else "2"}'
    selected_quincena = default_quincena
    selected_merge_strategy = DTR_UPLOAD_MERGE_FILL_BLANKS
    can_force_processed = _dtr_upload_force_processed_allowed()
    if request.method == 'POST':
        selected_quincena = (request.form.get('upload_quincena') or '').strip() or default_quincena
        selected_merge_strategy = _normalize_dtr_upload_merge_strategy(
            request.form.get('merge_strategy')
        )
        force_processed = bool(request.form.get('force_processed')) and can_force_processed
        q_year, q_month, q_half = _parse_quincena_upload_value(selected_quincena)
        if q_year is None:
            flash('Invalid quincena selection.', 'error')
            q_year, q_month, q_half = _parse_quincena_upload_value(default_quincena)
            selected_quincena = default_quincena
        period_err = _dtr_upload_period_gate(selected_quincena, force_processed=force_processed)
        f = request.files.get('dtr_file')
        if period_err:
            flash(period_err, 'error')
        elif f and f.filename:
            uploaded_filename = f.filename or ''
            try:
                content = f.read()
                parsed_rows = _parse_dtr_dat_file(content, uploaded_filename)
                if not parsed_rows:
                    flash('No valid DTR records found in the file.', 'warning')
                else:
                    row_dates = set()
                    for row in parsed_rows:
                        try:
                            row_dates.add(datetime.strptime(row.get('date', ''), '%Y-%m-%d').date())
                        except ValueError:
                            continue
                    parse_summary = _dtr_upload_parse_summary(parsed_rows)
                    row_payload = _dtr_save_payload_from_upload_rows(parsed_rows)
                    filtered_payload, outside_dates, skipped_outside, filter_err = _filter_upload_rows_for_quincena(
                        row_payload, selected_quincena,
                    )
                    if filter_err:
                        flash(filter_err, 'error')
                    elif not filtered_payload:
                        flash(
                            'No records in the file fall within the selected quincena '
                            '(including ±1 day for night shift / flexitime). Nothing was saved.',
                            'warning',
                        )
                    else:
                        in_quincena_dates = _row_dates_in_core_quincena(
                            filtered_payload, q_year, q_month, q_half,
                        )
                        quincena_missing_dates = _missing_dates_for_quincena(
                            in_quincena_dates, q_year, q_month, q_half,
                        )
                        accept_start, accept_end, core_start, core_end = quincena_upload_accept_range(
                            q_year, q_month, q_half,
                        )
                        core_row_count = sum(
                            1 for row in filtered_payload
                            if (_d := _parse_dtr_upload_form_date(str(row.get('date') or '').strip()))
                            and core_start <= _d <= core_end
                        )
                        try:
                            commit = _dtr_upload_commit_rows(
                                filtered_payload,
                                selected_quincena,
                                filename=uploaded_filename,
                                record_count=len(filtered_payload),
                                merge_strategy=selected_merge_strategy,
                                force_processed=force_processed,
                            )
                            qy, qm, qh = _parse_quincena_upload_value(selected_quincena)
                            batch = DtrUploadBatch.query.get(commit['batch_id'])
                            item_count = batch.items.count() if batch else 0
                            can_undo = can_undo_dtr_batch(batch)
                            save_result = {
                                **parse_summary,
                                'saved': commit['saved'],
                                'error_count': commit['error_count'],
                                'batch_id': commit['batch_id'],
                                'created_count': commit['created_count'],
                                'merged_count': commit['merged_count'],
                                'unchanged_count': commit.get('unchanged_count', 0),
                                'merge_strategy': commit.get('merge_strategy', selected_merge_strategy),
                                'punch_flag_count': commit.get('punch_flag_count', 0),
                                'can_undo': can_undo,
                                'show_undo': can_undo and item_count > 0,
                                'filename': uploaded_filename,
                                'quincena': selected_quincena,
                                'quincena_label': (
                                    _jo_cos_payslip_period_label(qy, qm, str(qh))
                                    if qy is not None else selected_quincena
                                ),
                                'records_in_quincena': core_row_count,
                                'records_accepted': len(filtered_payload),
                                'buffer_records': len(filtered_payload) - core_row_count,
                                'accept_range_label': (
                                    f'{accept_start.strftime("%b %d, %Y")} – '
                                    f'{accept_end.strftime("%b %d, %Y")}'
                                ),
                                'skipped_outside_rows': skipped_outside,
                                'outside_dates': [d.strftime('%Y-%m-%d') for d in outside_dates],
                                'missing_dates': [d.strftime('%Y-%m-%d') for d in quincena_missing_dates],
                                'punch_flags_url': url_for(
                                    'routes.dtr_upload_punch_flags',
                                    batch_id=commit['batch_id'],
                                ),
                                'dtr_records_url': url_for(
                                    'routes.dtr_records',
                                    mode='quincena',
                                    year=qy or today.year,
                                    month=qm or today.month,
                                    quincena=str(qh or '1'),
                                ),
                            }
                            if commit['errors']:
                                for e in commit['errors'][:20]:
                                    flash(e, 'error')
                                if len(commit['errors']) > 20:
                                    flash(f'…and {len(commit["errors"]) - 20} more row error(s).', 'error')
                        except Exception as e:
                            db.session.rollback()
                            flash(f'Error saving DTR: {str(e)}', 'error')
            except Exception as e:
                flash(f'Error reading file: {str(e)}', 'error')
    recent_batches = []
    is_admin_hr = _user_is_admin_or_hr()
    can_review_punch_flags = _user_can_review_dtr_punch_flags()
    for b in list_recent_dtr_upload_batches(limit=15):
        item_count = b.items.count()
        can_undo = can_undo_dtr_batch(b)
        open_flags = count_open_punch_flags(b.id) if (b.status or '').strip().lower() == 'active' else 0
        recent_batches.append({
            'id': b.id,
            'filename': b.filename,
            'quincena_label': _dtr_upload_batch_label(b),
            'uploaded_at': b.uploaded_at,
            'uploaded_by': b.uploaded_by_username,
            'saved_count': b.saved_count,
            'created_count': b.created_count,
            'merged_count': b.merged_count,
            'item_count': item_count,
            'punch_flag_count': open_flags,
            'status': (b.status or 'active').strip().lower(),
            'can_undo': can_undo,
            'show_undo': can_undo and item_count > 0,
        })
    sqy, sqm, sqh = _parse_quincena_upload_value(selected_quincena)
    selected_period = (
        dtr_period.get_period(sqy, sqm, str(sqh))
        if sqy is not None and sqm is not None and sqh is not None
        else None
    )
    selected_period_locked = dtr_period.is_period_locked(selected_period)
    return render_template(
        'dtr_upload.html',
        employee=employee,
        save_result=save_result,
        recent_batches=recent_batches,
        is_admin_hr=is_admin_hr,
        can_review_punch_flags=can_review_punch_flags,
        can_force_processed=can_force_processed,
        selected_period_locked=selected_period_locked,
        selected_merge_strategy=selected_merge_strategy,
        quincena_options=quincena_options,
        selected_quincena=selected_quincena,
    )


@bp.route('/dtr-upload/batch/<int:batch_id>/undo', methods=['POST'])
@login_required
def dtr_upload_batch_undo(batch_id):
    if not _user_can_upload_dtr_log():
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    try:
        ok, msg, _stats = undo_dtr_upload_batch(batch_id)
        flash(msg, 'success' if ok else 'error')
    except Exception as e:
        db.session.rollback()
        flash(f'Undo failed: {str(e)}', 'error')
    return redirect(url_for('routes.dtr_upload'))


@bp.route('/dtr-upload/batch/<int:batch_id>/punch-flags')
@login_required
def dtr_upload_punch_flags(batch_id):
    """Admin/HR/Payroll Maker review of duplicate punches retained from an upload batch."""
    denied = _require_dtr_punch_flag_reviewer()
    if denied:
        return denied
    batch = DtrUploadBatch.query.get_or_404(batch_id)
    flags = list_punch_flags_for_batch(batch_id)

    emp_ids = {f.employee_id for f in flags}
    emp_map = {}
    if emp_ids:
        emp_map = {
            e.id: e
            for e in (
                Employee.query
                .options(db.joinedload(Employee.department))
                .filter(Employee.id.in_(emp_ids))
                .all()
            )
        }

    # Payroll Maker only sees employees in their own department
    dept_id = _payroll_maker_department_id()
    if dept_id is not None:
        flags = [
            f for f in flags
            if (emp_map.get(f.employee_id) and emp_map[f.employee_id].department_id == dept_id)
        ]
        open_count = sum(1 for f in flags if (f.status or '').strip().lower() == 'open')
    else:
        open_count = count_open_punch_flags(batch_id)

    status_filter = (request.args.get('status') or 'open').strip().lower()
    if status_filter in ('open', 'resolved', 'inactive'):
        flags = [f for f in flags if (f.status or '').strip().lower() == status_filter]
    elif status_filter != 'all':
        status_filter = 'open'
        flags = [f for f in flags if (f.status or '').strip().lower() == 'open']

    # DTR "Multiple punches" link passes employee_id (badge) — filter server-side
    # so we do not build/render the entire batch for one person.
    filter_badge = (request.args.get('employee_id') or request.args.get('q') or '').strip()
    if filter_badge:
        badge_l = filter_badge.lower()
        matched_emp_ids = set()
        for eid, emp in emp_map.items():
            badge = (emp.employee_id or '').strip().lower()
            name = (emp.directory_name or '').lower()
            if badge_l == badge or badge_l in badge or badge_l in name:
                matched_emp_ids.add(eid)
        if matched_emp_ids:
            flags = [f for f in flags if f.employee_id in matched_emp_ids]

    qy, qm, qh = _parse_quincena_upload_value(batch.quincena or '')
    period = (
        dtr_period.get_period(qy, qm, str(qh))
        if qy is not None and qm is not None and qh is not None
        else None
    )
    period_locked = dtr_period.is_period_locked(period)

    # Group flags by employee-day for one four-slot assignment editor per day
    day_groups = {}
    for flag in flags:
        day_groups.setdefault((flag.employee_id, flag.record_date), []).append(flag)

    scoped_emp_ids = {emp_id for emp_id, _ in day_groups.keys()}
    punches_by_day = punches_by_employee_day_for_batch(
        batch_id,
        employee_ids=scoped_emp_ids,
    )

    dtr_by_day = {}
    if day_groups:
        day_dates = [rec_date for _, rec_date in day_groups.keys()]
        min_d, max_d = min(day_dates), max(day_dates)
        dtr_rows = (
            DailyTimeRecord.query
            .filter(DailyTimeRecord.employee_id.in_(scoped_emp_ids))
            .filter(DailyTimeRecord.record_date >= min_d)
            .filter(DailyTimeRecord.record_date <= max_d)
            .all()
        )
        dtr_by_day = {(r.employee_id, r.record_date): r for r in dtr_rows}

    day_rows = []
    for (emp_id, rec_date), day_flags in sorted(
        day_groups.items(),
        key=lambda item: (item[0][1], item[0][0]),
    ):
        emp = emp_map.get(emp_id)
        punches = punches_by_day.get((emp_id, rec_date), [])
        dtr = dtr_by_day.get((emp_id, rec_date))
        current_slots = {
            'am_in': dtr.am_in if dtr else None,
            'am_out': dtr.am_out if dtr else None,
            'pm_in': dtr.pm_in if dtr else None,
            'pm_out': dtr.pm_out if dtr else None,
        }
        statuses = {(f.status or '').strip().lower() for f in day_flags}
        if 'open' in statuses:
            day_status = 'open'
        elif 'resolved' in statuses and len(statuses) == 1:
            day_status = 'resolved'
        elif 'inactive' in statuses and len(statuses) == 1:
            day_status = 'inactive'
        else:
            day_status = 'mixed'
        punch_count = len(punches)
        slot_labels_flagged = [
            DTR_PUNCH_SLOT_LABELS.get(f.slot, f.slot) for f in day_flags
        ]
        day_key = f'{emp_id}-{rec_date.isoformat()}'
        day_rows.append({
            'day_key': day_key,
            'employee_id': emp_id,
            'record_date': rec_date,
            'employee': emp,
            'employee_name': (
                emp.directory_name if emp else f'#{emp_id}'
            ),
            'badge_id': (emp.employee_id if emp else ''),
            'flags': day_flags,
            'flagged_slots': slot_labels_flagged,
            'punch_count': punch_count,
            'punches': punches,
            'current_slots': current_slots,
            'day_status': day_status,
            'can_edit': (
                day_status in ('open', 'resolved', 'mixed')
                and not period_locked
                and (batch.status or '').strip().lower() == 'active'
            ),
            'dtr_records_url': url_for(
                'routes.dtr_records',
                employee_id=emp_id,
                mode='quincena' if qh else 'month',
                year=rec_date.year,
                month=rec_date.month,
                quincena=str(qh or ('1' if rec_date.day <= 15 else '2')),
            ),
        })

    employee = current_user.employee if current_user.employee else None
    back_url = (
        url_for('routes.dtr_upload')
        if _user_can_upload_dtr_log() or _user_is_admin_or_hr()
        else url_for('routes.dtr_records')
    )
    return render_template(
        'dtr_upload_punch_flags.html',
        employee=employee,
        batch=batch,
        batch_label=_dtr_upload_batch_label(batch),
        day_rows=day_rows,
        status_filter=status_filter,
        open_count=open_count,
        period_locked=period_locked,
        slot_labels=DTR_PUNCH_SLOT_LABELS,
        back_url=back_url,
        can_review_punch_flags=True,
        is_payroll_maker_scope=dept_id is not None,
        filter_badge=filter_badge,
    )


@bp.route(
    '/dtr-upload/batch/<int:batch_id>/punch-flags/day/<int:employee_id>/<string:record_date>/resolve',
    methods=['POST'],
)
@login_required
def dtr_upload_punch_flag_day_resolve(batch_id, employee_id, record_date):
    """Assign retained punches across all four DTR slots for one employee-day."""
    denied = _require_dtr_punch_flag_reviewer()
    if denied:
        return denied
    batch = DtrUploadBatch.query.get_or_404(batch_id)
    emp = Employee.query.get_or_404(employee_id)
    if not _user_can_access_punch_flag_employee(emp):
        flash('Access denied for this employee punch flag.', 'error')
        return redirect(url_for('routes.dtr_upload_punch_flags', batch_id=batch.id))

    try:
        record_date = datetime.strptime(str(record_date).strip(), '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid record date.', 'error')
        return redirect(url_for('routes.dtr_upload_punch_flags', batch_id=batch.id))

    qy, qm, qh = _parse_quincena_upload_value(batch.quincena or '')
    if qy is not None and qm is not None and qh is not None:
        period = dtr_period.get_period(qy, qm, str(qh))
        if dtr_period.is_period_locked(period):
            flash(
                'This quincena has been processed. Reopen it from Quincena Processing before correcting punches.',
                'error',
            )
            return redirect(url_for('routes.dtr_upload_punch_flags', batch_id=batch.id))

    slot_choices = {}
    for slot in DTR_PUNCH_SLOT_LABELS:
        raw = (request.form.get(f'slot_{slot}') or 'keep').strip()
        if raw.lower() in ('keep', 'clear'):
            slot_choices[slot] = raw.lower()
        else:
            try:
                slot_choices[slot] = int(raw)
            except ValueError:
                flash(f'Invalid selection for {DTR_PUNCH_SLOT_LABELS[slot]}.', 'error')
                return redirect(url_for(
                    'routes.dtr_upload_punch_flags',
                    batch_id=batch.id,
                    status=(request.form.get('return_status') or 'open').strip() or 'open',
                    employee_id=(emp.employee_id or ''),
                ))

    try:
        ok, msg = apply_day_punch_assignments(
            batch_id=batch.id,
            employee_id=employee_id,
            record_date=record_date,
            slot_choices=slot_choices,
            user=current_user,
        )
        if not ok:
            db.session.rollback()
            flash(msg, 'error')
            return redirect(url_for(
                'routes.dtr_upload_punch_flags',
                batch_id=batch.id,
                status='open',
                employee_id=(emp.employee_id or ''),
            ))

        sched = _pick_late_undertime_schedule(emp, record_date)
        dtr = DailyTimeRecord.query.filter_by(
            employee_id=employee_id,
            record_date=record_date,
        ).first()
        if dtr:
            late_m, ut_m = _late_undertime_minutes_for_record(dtr, sched)
            tot = late_m + ut_m
            dtr.undertime_hrs = tot // 60
            dtr.undertime_mins = tot % 60
        db.session.commit()
        flash(msg, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Could not assign punches: {str(e)}', 'error')
    return redirect(url_for(
        'routes.dtr_upload_punch_flags',
        batch_id=batch.id,
        status=(request.form.get('return_status') or 'open').strip() or 'open',
        employee_id=(emp.employee_id or ''),
    ))


@bp.route('/dtr-upload/punch-flags/<int:flag_id>/resolve', methods=['POST'])
@login_required
def dtr_upload_punch_flag_resolve(flag_id):
    """Legacy single-slot resolve — still supported via day assignment wrapper."""
    denied = _require_dtr_punch_flag_reviewer()
    if denied:
        return denied
    flag = DtrUploadPunchFlag.query.get_or_404(flag_id)
    batch = DtrUploadBatch.query.get(flag.batch_id)
    if not batch:
        flash('Upload batch not found.', 'error')
        return redirect(url_for('routes.dtr_records'))

    emp = Employee.query.get(flag.employee_id)
    if not _user_can_access_punch_flag_employee(emp):
        flash('Access denied for this employee punch flag.', 'error')
        return redirect(url_for('routes.dtr_upload_punch_flags', batch_id=batch.id))

    qy, qm, qh = _parse_quincena_upload_value(batch.quincena or '')
    if qy is not None and qm is not None and qh is not None:
        period = dtr_period.get_period(qy, qm, str(qh))
        if dtr_period.is_period_locked(period):
            flash(
                'This quincena has been processed. Reopen it from Quincena Processing before correcting punches.',
                'error',
            )
            return redirect(url_for('routes.dtr_upload_punch_flags', batch_id=batch.id))

    try:
        punch_id = int((request.form.get('punch_id') or '0').strip())
    except ValueError:
        punch_id = 0
    punch = DtrUploadPunch.query.get(punch_id) if punch_id else None
    if not punch:
        flash('Select a punch timestamp to apply.', 'error')
        return redirect(url_for('routes.dtr_upload_punch_flags', batch_id=batch.id, status='open'))

    try:
        ok, msg = apply_selected_punch_to_dtr(flag, punch, user=current_user)
        if not ok:
            db.session.rollback()
            flash(msg, 'error')
            return redirect(url_for('routes.dtr_upload_punch_flags', batch_id=batch.id, status='open'))

        if emp:
            sched = _pick_late_undertime_schedule(emp, flag.record_date)
            dtr = DailyTimeRecord.query.filter_by(
                employee_id=flag.employee_id,
                record_date=flag.record_date,
            ).first()
            if dtr:
                late_m, ut_m = _late_undertime_minutes_for_record(dtr, sched)
                tot = late_m + ut_m
                dtr.undertime_hrs = tot // 60
                dtr.undertime_mins = tot % 60
        db.session.commit()
        flash(msg, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Could not resolve punch flag: {str(e)}', 'error')
    return redirect(url_for(
        'routes.dtr_upload_punch_flags',
        batch_id=batch.id,
        status=(request.form.get('return_status') or 'open').strip() or 'open',
    ))


@bp.route('/dtr-upload/save', methods=['POST'])
@login_required
def dtr_upload_save():
    if not _user_can_upload_dtr_log():
        flash('Access denied.', 'error')
        ct0 = (request.content_type or '').lower()
        if ct0.startswith('application/json'):
            return jsonify(ok=False, error='Access denied.'), 403
        return redirect(url_for(_dashboard_for_user(current_user)))
    ct = (request.content_type or '').lower()
    wants_json = ct.startswith('application/json')
    upload_token = ''

    merge_strategy = DTR_UPLOAD_MERGE_FILL_BLANKS
    force_processed = False
    if wants_json:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error='Invalid JSON body'), 400
        upload_quincena = (body.get('upload_quincena') or '').strip()
        upload_token = (body.get('upload_token') or '').strip()
        merge_strategy = _normalize_dtr_upload_merge_strategy(body.get('merge_strategy'))
        force_processed = bool(body.get('force_processed')) and _dtr_upload_force_processed_allowed()
        if upload_token:
            row_data, parse_err = load_staged_dtr_upload_rows(upload_token, _dtr_upload_owner_key())
        else:
            row_data, parse_err = _dtr_rows_from_payload_list(body.get('rows'))
    else:
        upload_quincena = (request.form.get('upload_quincena') or '').strip()
        merge_strategy = _normalize_dtr_upload_merge_strategy(request.form.get('merge_strategy'))
        force_processed = bool(request.form.get('force_processed')) and _dtr_upload_force_processed_allowed()
        row_data, parse_err = _dtr_upload_save_parse_form_rows(request)

    if _parse_quincena_upload_value(upload_quincena)[0] is None:
        msg = 'Missing or invalid quincena selection.'
        if wants_json:
            return jsonify(ok=False, error=msg), 400
        flash(msg, 'error')
        return redirect(url_for('routes.dtr_upload'))

    if parse_err:
        if wants_json:
            return jsonify(ok=False, error=parse_err), 400
        flash(parse_err, 'error')
        return redirect(url_for('routes.dtr_upload'))

    period_err = _dtr_upload_period_gate(upload_quincena, force_processed=force_processed)
    if period_err:
        if wants_json:
            return jsonify(ok=False, error=period_err), 400
        flash(period_err, 'error')
        return redirect(url_for('routes.dtr_upload'))

    quincena_err = None
    filtered_rows, outside_dates, skipped_outside, filter_err = _filter_upload_rows_for_quincena(row_data, upload_quincena)
    if filter_err:
        quincena_err = filter_err
    elif not filtered_rows:
        quincena_err = (
            'No records fall within the selected quincena '
            '(including ±1 day for night shift / flexitime). Nothing was saved.'
        )
    if quincena_err:
        if wants_json:
            return jsonify(ok=False, error=quincena_err), 400
        flash(quincena_err, 'error' if filter_err else 'warning')
        return redirect(url_for('routes.dtr_upload'))

    try:
        commit = _dtr_upload_commit_rows(
            filtered_rows,
            upload_quincena,
            merge_strategy=merge_strategy,
            force_processed=force_processed,
        )
        if commit['errors']:
            for e in commit['errors']:
                flash(e, 'error')
        if wants_json and upload_token:
            discard_staged_dtr_upload(upload_token)
        saved = commit['saved']
        flash(
            f'Processed {saved} DTR record(s) '
            f'({commit["created_count"]} new, {commit["merged_count"]} updated, '
            f'{commit.get("unchanged_count", 0)} unchanged).',
            'success',
        )
        if wants_json:
            return jsonify(
                ok=True,
                redirect=url_for('routes.dtr_upload'),
                saved=saved,
                created_count=commit['created_count'],
                merged_count=commit['merged_count'],
                unchanged_count=commit.get('unchanged_count', 0),
                merge_strategy=commit.get('merge_strategy'),
                error_count=commit['error_count'],
            )
        return redirect(url_for('routes.dtr_upload'))
    except Exception as e:
        db.session.rollback()
        if wants_json:
            return jsonify(ok=False, error=f'Error saving DTR: {str(e)}'), 500
        flash(f'Error saving DTR: {str(e)}', 'error')
        return redirect(url_for('routes.dtr_upload'))


@bp.route('/dtr/generate', methods=['POST'])
@login_required
def dtr_generate():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))

    mode = (request.form.get('mode') or 'quincena').strip().lower()
    month = int((request.form.get('month') or str(date.today().month)).strip())
    year = int((request.form.get('year') or str(date.today().year)).strip())
    quincena = (request.form.get('quincena') or '1').strip()
    if quincena not in ('1', '2'):
        flash('Invalid quincena.', 'error')
        return redirect(url_for('routes.dashboard'))
    if mode == 'month':
        flash('Monthly DTR generation is no longer supported. Use quincena preparation for all employees.', 'error')
        return redirect(url_for('routes.dashboard'))

    try:
        start_d, end_d = _quincena_date_range(year, month, quincena)
        employees = Employee.query.filter_by(status='active').all()

        workweek_segments, seg_err = dtr_wa.parse_segments_from_form(request.form, start_d, end_d)
        if seg_err:
            flash(seg_err, 'error')
            return redirect(url_for('routes.dashboard'))

        if workweek_segments:
            dtr_wa.save_workweek_segments(
                workweek_segments,
                current_user.id if current_user and getattr(current_user, 'id', None) else None,
            )

        # One entry per employee id (avoids duplicate INSERT same key in one batch).
        employees = list({e.id: e for e in employees}.values())
        employees.sort(key=lambda e: ((e.last_name or '').lower(), (e.first_name or '').lower(), e.id))

        holiday_raw = (request.form.get('holiday_dates') or '').strip()
        suspension_raw = (request.form.get('work_suspension_dates') or '').strip()
        holiday_entries = _parse_dtr_special_days_for_generate(holiday_raw, start_d, end_d)
        suspension_entries = _parse_dtr_special_days_for_generate(suspension_raw, start_d, end_d)
        special_dates = _dtr_special_dates_from_entries(holiday_entries, suspension_entries)

        created = 0
        with db.session.no_autoflush:
            curr = start_d
            while curr <= end_d:
                for emp in employees:
                    exists = _dtr_find_row_by_employee_day_readonly(emp, curr)
                    if not exists:
                        db.session.add(DailyTimeRecord(employee_id=emp.id, record_date=curr))
                        created += 1
                curr += timedelta(days=1)

        if workweek_segments:
            dtr_wa.apply_workweek_segments_to_dtr(
                workweek_segments,
                employees,
                start_d,
                end_d,
                special_dates,
                find_row=_dtr_find_row_by_employee_day_readonly,
                skip_stamp=_dtr_skip_arrangement_stamp_for_row,
                matches_applies=_employee_matches_dtr_applies_to,
                skip_duty_stamp=_is_jo_cos_employee,
            )

        holiday_marked = 0
        suspension_marked = 0
        if holiday_entries or suspension_entries:
            db.session.flush()
            # Holidays / work suspensions are org-wide — all active employees.
            all_active = (
                Employee.query.filter_by(status='active')
                .order_by(Employee.last_name.asc(), Employee.first_name.asc())
                .all()
            )
            for h, half in holiday_entries:
                for emp in all_active:
                    rec, was_created = _dtr_get_or_create_row_for_special_day(emp, h)
                    if was_created:
                        created += 1
                    _apply_dtr_special_day(rec, 'HOLIDAY', half)
                    holiday_marked += 1
            for s, half in suspension_entries:
                for emp in all_active:
                    rec, was_created = _dtr_get_or_create_row_for_special_day(emp, s)
                    if was_created:
                        created += 1
                    _apply_dtr_special_day(rec, 'WORK SUSPENSION', half)
                    suspension_marked += 1

        db.session.commit()
        q_label = '1st' if quincena == '1' else '2nd'
        msg = (
            f'DTR generated for {len(employees)} employee(s) ({q_label} quincena, '
            f'{start_d.strftime("%Y-%m-%d")} to {end_d.strftime("%Y-%m-%d")}). '
            f'New rows created: {created}.'
        )
        seg_msg = dtr_wa.format_segments_flash_message(workweek_segments)
        if seg_msg:
            msg += f' {seg_msg}'
        if holiday_entries:
            msg += f' Holidays marked: {len(holiday_entries)} entry/entries, {holiday_marked} row(s).'
        if suspension_entries:
            msg += f' Work suspensions marked: {len(suspension_entries)} entry/entries, {suspension_marked} row(s).'
        flash(msg, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating DTR: {str(e)}', 'error')
    return redirect(url_for('routes.dashboard'))


def _build_dtr_view_rows(emp: Employee, start_d: date, end_d: date) -> tuple[list, dict, dict]:
    """Build DTR grid rows, summary counts, and justification map for one employee."""
    rows = []
    summary = {'lates': 0, 'undertimes': 0, 'absences': 0, 'duplicate_punches': 0}
    _prefetch_flexi_schedule_rows_for_range(emp, start_d, end_d)
    dtr_rows = _dtr_rows_for_employee(emp, start_d, end_d)
    by_date = {r.record_date: r for r in dtr_rows}
    approved_leave_dates = _approved_leave_dates_for_employee(emp.id, start_d, end_d)
    just_map = {
        j.record_date: j for j in (
            DtrJustification.query
            .filter(DtrJustification.employee_id == emp.id)
            .filter(DtrJustification.record_date >= start_d)
            .filter(DtrJustification.record_date <= end_d)
            .all()
        )
    }
    punch_flags_by_date = reviewable_punch_flags_by_date(emp.id, start_d, end_d)
    curr = start_d
    while curr <= end_d:
        rec = by_date.get(curr)
        dow = curr.weekday()
        is_weekend = dow >= 5
        remarks = (rec.remarks if rec and rec.remarks else ('SATURDAY' if dow == 5 else ('SUNDAY' if dow == 6 else '')))
        if curr in approved_leave_dates and not remarks:
            remarks = 'APPROVED LEAVE'
        elif (
            not remarks
            and curr.weekday() == 4
            and _arrangement_model_code_for_date(emp, curr) == '4day'
            and not (rec and any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]))
            and not (rec and _dtr_is_special_day_remark(rec.remarks))
        ):
            remarks = 'REST DAY'
        late = False
        undertime = False
        absent = False
        late_mins = 0
        undertime_mins = 0
        flexi_shift_code = None
        flexi_assigned = False
        flexi_rest_day = False
        sched_am_in = sched_am_out = sched_pm_in = sched_pm_out = ''
        show_flexi_slots = _employee_is_flexible_for_date(emp, curr)
        flexi_list_sched = _flexible_worktime_duty_schedule_for_date(emp, curr)
        flexi_row = _flexi_schedule_row_for_employee_date(emp, curr)
        if flexi_row:
            flexi_assigned = True
            flexi_shift_code = (flexi_row.shift_code or '').strip() or None
        if flexi_list_sched and _duty_schedule_is_rest(flexi_list_sched):
            flexi_rest_day = True
            if not remarks or remarks in ('SATURDAY', 'SUNDAY'):
                remarks = 'REST DAY'
            show_flexi_slots = True
        elif not is_weekend or flexi_list_sched:
            # Weekdays always; weekends only when a flexi duty schedule covers the date.
            schedule = flexi_list_sched or _pick_late_undertime_schedule(emp, curr)
            if show_flexi_slots and schedule and not _duty_schedule_is_rest(schedule):
                sched_am_in, sched_am_out, sched_pm_in, sched_pm_out = _duty_schedule_time_strings(schedule)
            if schedule and not _duty_schedule_is_rest(schedule):
                if rec:
                    late_mins, undertime_mins = _late_undertime_minutes_for_record(rec, schedule)
                    late = late_mins > 0
                    undertime = undertime_mins > 0
                absent = (
                    (not rec or not any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out]))
                    and not remarks
                )
            if late:
                summary['lates'] += 1
            if absent:
                summary['absences'] += 1
            if undertime:
                summary['undertimes'] += 1
        day_punch_flags = punch_flags_by_date.get(curr) or []
        has_duplicate_punches = bool(day_punch_flags)
        if has_duplicate_punches:
            summary['duplicate_punches'] += 1
        day_flag_statuses = {(f.status or '').strip().lower() for f in day_punch_flags}
        if 'open' in day_flag_statuses:
            punch_review_status = 'open'
        elif day_punch_flags:
            punch_review_status = 'resolved'
        else:
            punch_review_status = 'open'
        just_row = just_map.get(curr)
        rows.append({
            'date': curr,
            'am_in': rec.am_in if rec else None,
            'am_out': rec.am_out if rec else None,
            'pm_in': rec.pm_in if rec else None,
            'pm_out': rec.pm_out if rec else None,
            'undertime': (
                f"{((late_mins + undertime_mins) // 60):02d} / {((late_mins + undertime_mins) % 60):02d}"
                if rec else ''
            ),
            'remarks': remarks,
            'late': late,
            'undertime_flag': undertime,
            'absent': absent,
            'has_justification': curr in just_map,
            'justification_status': (just_row.status if just_row else ''),
            'justification_reason': (just_row.reason if just_row else ''),
            'is_flagged': late or undertime or absent,
            'has_duplicate_punches': has_duplicate_punches,
            'duplicate_punch_count': len(day_punch_flags),
            'duplicate_punch_slots': [
                DTR_PUNCH_SLOT_LABELS.get(f.slot, f.slot) for f in day_punch_flags
            ],
            'duplicate_punch_batch_id': day_punch_flags[0].batch_id if day_punch_flags else None,
            'duplicate_punch_review_status': punch_review_status,
            'show_flexi_slots': show_flexi_slots and (not is_weekend or bool(flexi_list_sched)),
            'flexi_rest_day': flexi_rest_day,
            'sched_am_in': sched_am_in,
            'sched_am_out': sched_am_out,
            'sched_pm_in': sched_pm_in,
            'sched_pm_out': sched_pm_out,
            'flexi_shift_code': flexi_shift_code,
            'flexi_assigned': flexi_assigned,
            'stored_remarks': (rec.remarks or '') if rec else '',
            'has_saved_row': rec is not None,
        })
        curr += timedelta(days=1)
    return rows, summary, just_map


def _csc_dtr_employee_name(emp: Employee) -> str:
    first = (emp.first_name or '').strip()
    mid = (emp.middle_name or '').strip()
    last = (emp.last_name or '').strip()
    if mid:
        return f'{first} {mid[0].upper()}. {last}'.upper()
    return f'{first} {last}'.upper().strip()


def _csc_dtr_department_head_name(emp: Employee) -> str:
    """Printed name of the employee's department manager (blank if none assigned)."""
    dept = getattr(emp, 'department', None)
    if dept is None:
        return ''
    mgr = getattr(dept, 'manager', None)
    if mgr is None:
        mgr_id = getattr(dept, 'manager_id', None)
        if mgr_id:
            mgr = Employee.query.get(mgr_id)
    if mgr is None:
        return ''
    return _csc_dtr_employee_name(mgr)


def _csc_dtr_is_department_head(emp: Employee) -> bool:
    if emp is None or not getattr(emp, 'id', None):
        return False
    return Department.query.filter_by(manager_id=emp.id).first() is not None


def _csc_dtr_mayor_employee() -> Employee | None:
    """Mayor as assigned manager of the Mayor's Office / MO department."""
    from sqlalchemy import func as sql_func

    mo_dept = (
        Department.query
        .filter(sql_func.lower(Department.name).in_(
            ['mo', "mayor's office", 'mayors office', 'municipal mayor']
        ))
        .first()
    )
    if not mo_dept:
        mo_dept = (
            Department.query
            .filter(sql_func.lower(Department.name).like('%mayor%'))
            .first()
        )
    if not mo_dept:
        return None
    mgr = getattr(mo_dept, 'manager', None)
    if mgr is None and getattr(mo_dept, 'manager_id', None):
        mgr = Employee.query.get(mo_dept.manager_id)
    return mgr


def _csc_dtr_supervisor_name(emp: Employee) -> str:
    """Supervisor line: department head, or the mayor when the employee is a department head."""
    if _csc_dtr_is_department_head(emp):
        mayor = _csc_dtr_mayor_employee()
        if mayor:
            return _csc_dtr_employee_name(mayor)
    return _csc_dtr_department_head_name(emp)


def _csc_dtr_period_label(start_d: date, end_d: date) -> str:
    return f'{start_d.strftime("%B %d")} - {end_d.strftime("%d, %Y")}'


def _csc_dtr_working_hours_lines(emp: Employee, start_d: date, end_d: date) -> list[str]:
    """Working Hours lines from quincena workweek segments (4-day / 5-day).

    Example:
      4-day workweek (May 01 - 15, 2026 | 7AM - 12PM | 1PM - 6PM)
      5-day workweek (May 01 - 07, 2026 | 8AM - 12PM | 1PM - 5PM)
    """
    day_codes: list[tuple[date, str]] = []
    curr = start_d
    while curr <= end_d:
        code = (_arrangement_model_code_for_date(emp, curr) or '5day').strip().lower()
        if code not in ('4day', '5day'):
            code = '5day'
        day_codes.append((curr, code))
        curr += timedelta(days=1)

    if not day_codes:
        return [
            f'5-day workweek ({_csc_dtr_period_label(start_d, end_d)} | 8AM - 12PM | 1PM - 5PM)'
        ]

    ranges: list[tuple[date, date, str]] = []
    seg_start, seg_code = day_codes[0]
    prev = day_codes[0][0]
    for d, code in day_codes[1:]:
        if code == seg_code and d == prev + timedelta(days=1):
            prev = d
            continue
        ranges.append((seg_start, prev, seg_code))
        seg_start, seg_code = d, code
        prev = d
    ranges.append((seg_start, prev, seg_code))

    def _hours(code: str) -> str:
        if code == '4day':
            return '7AM - 12PM | 1PM - 6PM'
        return '8AM - 12PM | 1PM - 5PM'

    def _week(code: str) -> str:
        return '4-day workweek' if code == '4day' else '5-day workweek'

    lines = []
    for s, e, code in ranges:
        lines.append(
            f'{_week(code)} ({_csc_dtr_period_label(s, e)} | {_hours(code)})'
        )
    return lines


def _csc_dtr_time_str(t) -> str:
    """Display punch times as hours:minutes only (no seconds)."""
    if not t:
        return ''
    return t.strftime('%I:%M %p')


@bp.app_template_filter('dtr_punch_time')
def dtr_punch_time_filter(t) -> str:
    return _csc_dtr_time_str(t)


def _csc_dtr_span_status(remarks: str) -> str | None:
    """Weekends / holidays / rest show as centered text across AM/PM columns."""
    r = (remarks or '').strip().upper()
    if not r:
        return None
    if r in ('SATURDAY', 'SUNDAY', 'HOLIDAY', 'REST DAY', 'APPROVED LEAVE', 'WORK SUSPENSION'):
        return r
    if r.startswith('HOLIDAY') or r.startswith('WORK SUSPENSION'):
        return r
    return None


def _dtr_csc_form48_pdf_bytes(
    emp: Employee,
    rows: list[dict],
    *,
    start_d: date,
    end_d: date,
    remarks_style: str = 'jo_cos',
) -> io.BytesIO:
    """Civil Service Form No. 48 layout (JO/COS and plantilla monthly DTR).

    ``remarks_style``:
      - ``jo_cos`` — last column is Remarks / Accomplishment (justification + report)
      - ``plantilla`` — last column is Remarks (justification reason only)

    Scales row height so a full-month printout fits on a single letter page.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    left = 28
    right = width - 28
    table_w = right - left
    # DATE | AM IN | AM OUT | PM IN | PM OUT | UNDERTIME | Remarks/Accomplishment
    col_w = [32, 64, 64, 64, 64, 66, table_w - (32 + 64 * 4 + 66)]
    col_x = [left]
    for w in col_w[:-1]:
        col_x.append(col_x[-1] + w)

    name = _csc_dtr_employee_name(emp)
    period = _csc_dtr_period_label(start_d, end_d)
    hours_lines = _csc_dtr_working_hours_lines(emp, start_d, end_d)
    n_rows = max(len(rows), 1)
    compact = n_rows > 16

    # --- Header (compact when printing a full month) ---
    y = height - (36 if compact else 48)
    c.setFont('Helvetica', 7 if compact else 8)
    c.drawCentredString(width / 2, y, 'CIVIL SERVICE FORM NO. 48')
    y -= 11 if compact else 14
    c.setFont('Helvetica-Bold', 10 if compact else 12)
    c.drawCentredString(width / 2, y, 'DAILY TIME RECORD')
    y -= 10 if compact else 12
    c.setFont('Helvetica-Bold', 7 if compact else 8)
    c.drawCentredString(width / 2, y, 'MUNICIPALITY OF JAGNA')
    y -= 14 if compact else 20
    c.setFont('Helvetica-Bold', 11 if compact else 14)
    c.drawCentredString(width / 2, y, name)
    y -= 12 if compact else 15

    meta_size = 8 if compact else 9
    c.setFont('Helvetica', meta_size)
    id_label = 'ID Number: '
    id_val = str(emp.employee_id or '')
    id_full_w = c.stringWidth(id_label, 'Helvetica', meta_size) + c.stringWidth(id_val, 'Helvetica-Bold', meta_size)
    id_x = (width - id_full_w) / 2
    c.drawString(id_x, y, id_label)
    c.setFont('Helvetica-Bold', meta_size)
    c.drawString(id_x + c.stringWidth(id_label, 'Helvetica', meta_size), y, id_val)
    y -= 11 if compact else 13

    c.setFont('Helvetica', meta_size)
    prefix = 'For the period of '
    pre_w = c.stringWidth(prefix, 'Helvetica', meta_size)
    per_w = c.stringWidth(period, 'Helvetica-Bold', meta_size)
    px0 = (width - (pre_w + per_w)) / 2
    c.drawString(px0, y, prefix)
    c.setFont('Helvetica-Bold', meta_size)
    c.drawString(px0 + pre_w, y, period)
    y -= 11 if compact else 13

    wh_prefix = 'Working Hours: '
    first = hours_lines[0] if hours_lines else '5-day workweek'
    font_size = 7 if (compact or len(first) > 70 or len(hours_lines) > 1) else 9
    c.setFont('Helvetica', font_size)
    wh_pre_w = c.stringWidth(wh_prefix, 'Helvetica', font_size)
    wh_val_w = c.stringWidth(first, 'Helvetica-Bold', font_size)
    wx0 = (width - (wh_pre_w + wh_val_w)) / 2
    c.drawString(wx0, y, wh_prefix)
    c.setFont('Helvetica-Bold', font_size)
    c.drawString(wx0 + wh_pre_w, y, first)
    for extra in hours_lines[1:]:
        y -= 9 if compact else 11
        c.setFont('Helvetica-Bold', font_size)
        c.drawCentredString(width / 2, y, extra)
    y -= 10 if compact else 14

    header_h = 20 if compact else 28
    plantilla_print = (remarks_style or '').strip().lower() == 'plantilla'
    # Leave room for certification + signature; size rows to fit remaining height
    footer_reserve = 108 if compact else 145
    if plantilla_print:
        footer_reserve += 42 if compact else 52
    table_top = y
    header_bottom = table_top - header_h
    available = max(header_bottom - footer_reserve, n_rows * 8.5)
    row_h = min(16.0 if not compact else 13.5, available / n_rows)
    row_h = max(row_h, 8.8)
    text_dy = max(2.2, row_h * 0.30)

    def _vline(x, y0, y1):
        c.line(x, y0, x, y1)

    def _hline(y_line, x0=left, x1=right):
        c.line(x0, y_line, x1, y_line)

    def _remarks_accomplishment_cell(row: dict, *, span_status: str | None) -> str:
        just = (row.get('justification_reason') or '').strip()
        if (remarks_style or '').strip().lower() == 'plantilla':
            return just
        parts: list[str] = []
        if just:
            parts.append(just)
        acc = (row.get('accomplishment_text') or '').strip()
        if acc:
            parts.append(acc)
        if not parts:
            rem = (row.get('remarks') or '').strip()
            if rem and not span_status:
                parts.append(rem)
        return ' | '.join(parts)

    remarks_header = (
        'Remarks'
        if (remarks_style or '').strip().lower() == 'plantilla'
        else 'Remarks / Accomplishment'
    )

    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.55 if compact else 0.7)
    c.rect(left, header_bottom, table_w, header_h, stroke=1, fill=0)
    mid_y = table_top - (10 if compact else 14)
    _hline(mid_y, col_x[1], col_x[5])
    _vline(col_x[1], header_bottom, table_top)
    _vline(col_x[2], header_bottom, mid_y)
    _vline(col_x[3], header_bottom, table_top)
    _vline(col_x[4], header_bottom, mid_y)
    _vline(col_x[5], header_bottom, table_top)
    _vline(col_x[6], header_bottom, table_top)

    hdr_main = 6.5 if compact else 7.5
    hdr_sub = 6 if compact else 7
    c.setFont('Helvetica-Bold', hdr_main)
    c.drawCentredString(col_x[0] + col_w[0] / 2, table_top - (12 if compact else 17), 'DATE')
    c.drawCentredString((col_x[1] + col_x[3]) / 2, table_top - (7 if compact else 10), 'AM')
    c.drawCentredString((col_x[3] + col_x[5]) / 2, table_top - (7 if compact else 10), 'PM')
    c.drawCentredString(col_x[5] + col_w[5] / 2, table_top - (8 if compact else 11), 'UNDERTIME')
    c.drawCentredString(col_x[5] + col_w[5] / 2, table_top - (15 if compact else 20), '(Hrs / Mins)')
    c.drawCentredString(col_x[6] + col_w[6] / 2, table_top - (11 if compact else 14), remarks_header)
    c.setFont('Helvetica-Bold', hdr_sub)
    c.drawCentredString(col_x[1] + col_w[1] / 2, header_bottom + 3, 'IN')
    c.drawCentredString(col_x[2] + col_w[2] / 2, header_bottom + 3, 'OUT')
    c.drawCentredString(col_x[3] + col_w[3] / 2, header_bottom + 3, 'IN')
    c.drawCentredString(col_x[4] + col_w[4] / 2, header_bottom + 3, 'OUT')

    y = header_bottom
    day_font = 7 if compact else 8
    time_font = 6 if compact else 7
    span_font = 7 if compact else 8
    ut_font = 6.5 if compact else 7.5
    rem_font = 5.5 if compact else 6

    for r in rows:
        row_bottom = y - row_h
        remarks = (r.get('remarks') or '').strip()
        span = _csc_dtr_span_status(remarks)
        am_in = _csc_dtr_time_str(r.get('am_in'))
        am_out = _csc_dtr_time_str(r.get('am_out'))
        pm_in = _csc_dtr_time_str(r.get('pm_in'))
        pm_out = _csc_dtr_time_str(r.get('pm_out'))
        has_punch = any([am_in, am_out, pm_in, pm_out])
        merge_ampm = bool(span and not has_punch)

        c.rect(left, row_bottom, table_w, row_h, stroke=1, fill=0)
        _vline(col_x[1], row_bottom, y)
        if not merge_ampm:
            _vline(col_x[2], row_bottom, y)
            _vline(col_x[3], row_bottom, y)
            _vline(col_x[4], row_bottom, y)
        _vline(col_x[5], row_bottom, y)
        _vline(col_x[6], row_bottom, y)

        day_num = f"{r['date'].day:02d}"
        c.setFont('Helvetica', day_font)
        c.drawCentredString(col_x[0] + col_w[0] / 2, row_bottom + text_dy, day_num)

        if merge_ampm:
            c.setFont('Helvetica', span_font)
            c.drawCentredString((col_x[1] + col_x[5]) / 2, row_bottom + text_dy, span)
            undertime = ''
            remarks_cell = _remarks_accomplishment_cell(r, span_status=span)
        else:
            c.setFont('Helvetica', time_font)
            c.drawCentredString(col_x[1] + col_w[1] / 2, row_bottom + text_dy, am_in)
            c.drawCentredString(col_x[2] + col_w[2] / 2, row_bottom + text_dy, am_out)
            c.drawCentredString(col_x[3] + col_w[3] / 2, row_bottom + text_dy, pm_in)
            c.drawCentredString(col_x[4] + col_w[4] / 2, row_bottom + text_dy, pm_out)
            if has_punch:
                undertime = (r.get('undertime') or '00 / 00').strip() or '00 / 00'
            elif (r['date'].weekday() < 5) and not span:
                undertime = '08 / 00'
            else:
                undertime = (r.get('undertime') or '').strip()
            remarks_cell = _remarks_accomplishment_cell(r, span_status=span)

        c.setFont('Helvetica', ut_font)
        c.drawCentredString(col_x[5] + col_w[5] / 2, row_bottom + text_dy, undertime)

        if remarks_cell:
            c.setFont('Helvetica', rem_font)
            max_w = col_w[6] - 3
            text = remarks_cell
            while c.stringWidth(text, 'Helvetica', rem_font) > max_w and len(text) > 3:
                text = text[:-2]
            if text != remarks_cell:
                text = text[:-1] + '…'
            c.drawCentredString(col_x[6] + col_w[6] / 2, row_bottom + text_dy, text)

        y = row_bottom

    # Certification footer — kept on same page
    y -= 14 if compact else 24
    cert_size = 7.5 if compact else 9
    c.setFont('Helvetica', cert_size)
    c.drawCentredString(width / 2, y, 'I certify on my honor that the above is a true and correct report of')
    y -= 10 if compact else 12
    c.drawCentredString(width / 2, y, 'the hours of work performed, record of which was made daily')
    y -= 10 if compact else 12
    c.drawCentredString(width / 2, y, 'at the time of arrival and departure from office.')
    sig_w = 200 if compact else 220
    c.setLineWidth(0.8)
    if plantilla_print:
        y -= 36 if compact else 48
        c.line(width / 2 - sig_w / 2, y, width / 2 + sig_w / 2, y)
        y -= 11 if compact else 13
        c.setFont('Helvetica-Bold', 10 if compact else 12)
        c.drawCentredString(width / 2, y, name)
        y -= 16 if compact else 22
        c.setFont('Helvetica', cert_size)
        c.drawCentredString(width / 2, y, 'Verified as to prescribed office hours')
        y -= 28 if compact else 36
        head_name = _csc_dtr_supervisor_name(emp)
        if head_name:
            c.setFont('Helvetica-Bold', 9 if compact else 10)
            c.drawCentredString(width / 2, y + 3, head_name)
        c.line(width / 2 - sig_w / 2, y, width / 2 + sig_w / 2, y)
        y -= 11 if compact else 13
        c.setFont('Helvetica-Bold', 8 if compact else 9)
        c.drawCentredString(width / 2, y, 'Supervisor')
    else:
        y -= 16 if compact else 24
        c.setFont('Helvetica-Bold', 10 if compact else 12)
        c.drawCentredString(width / 2, y, name)
        y -= 16 if compact else 24
        c.setFont('Helvetica', cert_size)
        c.drawCentredString(width / 2, y, 'Verified as to prescribed office hours')
        y -= 22 if compact else 32
        c.line(width / 2 - sig_w / 2, y, width / 2 + sig_w / 2, y)
        y -= 10 if compact else 12
        c.setFont('Helvetica-Bold', 8 if compact else 9)
        c.drawCentredString(width / 2, y, 'Supervisor')

    c.save()
    buf.seek(0)
    return buf


def _dtr_records_pdf_bytes(emp: Employee, rows: list, period_label: str, include_justifications: bool) -> io.BytesIO:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    y = height - 50
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, y, 'Daily Time Record')
    y -= 16
    c.setFont('Helvetica', 9)
    c.drawString(40, y, emp.full_name)
    y -= 12
    c.drawString(40, y, f'ID: {emp.employee_id}  |  Period: {period_label}')
    y -= 18
    c.setFont('Helvetica-Bold', 8)
    c.drawString(40, y, 'Day')
    c.drawString(70, y, 'AM In')
    c.drawString(120, y, 'AM Out')
    c.drawString(170, y, 'PM In')
    c.drawString(220, y, 'PM Out')
    c.drawString(270, y, 'Late/UT')
    c.drawString(320, y, 'Remarks')
    if include_justifications:
        c.drawString(420, y, 'Justification')
    y -= 10
    c.setFont('Helvetica', 7)
    for r in rows:
        if y < 50:
            c.showPage()
            y = height - 40
            c.setFont('Helvetica', 7)
        def _t(t):
            return t.strftime('%I:%M %p').lstrip('0') if t else ''
        c.drawString(40, y, str(r['date'].day))
        c.drawString(70, y, _t(r['am_in']))
        c.drawString(120, y, _t(r['am_out']))
        c.drawString(170, y, _t(r['pm_in']))
        c.drawString(220, y, _t(r['pm_out']))
        c.drawString(270, y, (r['undertime'] or '')[:8])
        c.drawString(320, y, (r['remarks'] or '')[:18])
        if include_justifications and r.get('has_justification'):
            st = (r.get('justification_status') or 'submitted').title()
            c.drawString(420, y, st[:14])
        y -= 10
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _pdf_wrap_text(text: str, max_chars: int) -> list[str]:
    words = (text or '').split()
    if not words:
        return ['']
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f'{current} {word}'
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _dtr_accomplishment_pdf_bytes(emp: Employee, export_rows: list[dict], period_label: str) -> io.BytesIO:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    y = height - 50
    c.setFont('Helvetica-Bold', 13)
    c.drawString(40, y, 'Accomplishment Report')
    y -= 16
    c.setFont('Helvetica', 9)
    c.drawString(40, y, emp.full_name)
    y -= 12
    c.drawString(40, y, f'Employee ID: {emp.employee_id}  |  Period: {period_label}')
    y -= 12
    c.drawString(40, y, 'Days shown: approved DTR justifications only.')
    y -= 20
    for row in export_rows:
        if y < 120:
            c.showPage()
            y = height - 50
        d = row['date']
        c.setFont('Helvetica-Bold', 8)
        c.drawString(40, y, d.strftime('%A, %B %d, %Y'))
        y -= 12
        c.setFont('Helvetica', 8)

        def _t(t):
            return t.strftime('%I:%M %p').lstrip('0') if t else '—'

        schedule = (
            f"AM {_t(row.get('am_in'))} – {_t(row.get('am_out'))}"
            f"  |  PM {_t(row.get('pm_in'))} – {_t(row.get('pm_out'))}"
        )
        for line in _pdf_wrap_text(schedule, 88):
            c.drawString(48, y, line)
            y -= 10
        if row.get('justification_reason'):
            c.setFont('Helvetica-Bold', 8)
            c.drawString(48, y, 'Justification:')
            y -= 10
            c.setFont('Helvetica', 8)
            for line in _pdf_wrap_text(row['justification_reason'], 82):
                c.drawString(56, y, line)
                y -= 10
        c.setFont('Helvetica-Bold', 8)
        c.drawString(48, y, 'Accomplishment:')
        y -= 10
        c.setFont('Helvetica', 8)
        body = row.get('accomplishment_text') or '(No report entered)'
        for line in _pdf_wrap_text(body, 82):
            if y < 60:
                c.showPage()
                y = height - 50
                c.setFont('Helvetica', 8)
            c.drawString(56, y, line)
            y -= 10
        y -= 8
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


@bp.route('/dtr/records')
@login_required
def dtr_records():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    employee = current_user.employee if current_user.employee else None
    viewer_emp = _current_employee_for_user()
    today = date.today()
    month = int((request.args.get('month') or str(today.month)).strip())
    year = int((request.args.get('year') or str(today.year)).strip())
    mode = (request.args.get('mode') or 'month').strip().lower()
    quincena = (request.args.get('quincena') or '1').strip()
    selected_employee_id = (request.args.get('employee_id') or '').strip()
    my_dtr_mode = (request.args.get('my') or '').strip() == '1'
    justify_date_raw = (request.args.get('justify_date') or '').strip()

    # Admin/HR/Payroll Maker may edit remarks only (edit=1). Punch times are never
    # free-edited; corrections go through duplicate-punch Use & Resolve.
    dtr_edit_mode = (
        role in ('admin', 'hr', 'payroll_maker')
        and (request.args.get('edit') or '').strip() == '1'
        and not my_dtr_mode
    )
    can_review_punch_flags = _user_can_review_dtr_punch_flags() and not my_dtr_mode

    if mode == 'quincena':
        start_d, end_d = _quincena_date_range(year, month, quincena)
    else:
        start_d, end_d = _month_date_range(year, month)

    quincena_period = None
    if mode == 'quincena':
        quincena_period = dtr_period.get_period(year, month, quincena)

    if my_dtr_mode:
        if not viewer_emp:
            flash('Employee record not linked to your account.', 'error')
            return redirect(url_for(_dashboard_for_user(current_user)))
        employee_q = Employee.query.filter_by(id=viewer_emp.id)
        req_mode = (request.args.get('mode') or 'quincena').strip().lower()
        if req_mode == 'month':
            mode = 'month'
            start_d, end_d = _month_date_range(year, month)
            quincena_period = None
        else:
            mode = 'quincena'
            start_d, end_d = _quincena_date_range(year, month, quincena)
            quincena_period = dtr_period.get_period(year, month, quincena)
    elif role in ('admin', 'hr'):
        employee_q = Employee.query.filter_by(status='active')
    elif role == 'payroll_maker':
        if not viewer_emp or not viewer_emp.department_id:
            employee_q = Employee.query.filter_by(id=-1)
        else:
            employee_q = Employee.query.filter_by(status='active', department_id=viewer_emp.department_id)
    elif role == 'manager':
        dept_ids = _managed_department_ids_for_manager()
        if dept_ids:
            employee_q = (Employee.query
                          .filter_by(status='active')
                          .filter(Employee.department_id.in_(dept_ids)))
        elif viewer_emp:
            employee_q = Employee.query.filter_by(id=viewer_emp.id)
        else:
            employee_q = Employee.query.filter_by(id=-1)
    else:
        if not viewer_emp:
            flash('Employee record not linked to your account.', 'error')
            return redirect(url_for(_dashboard_for_user(current_user)))
        employee_q = Employee.query.filter_by(id=viewer_emp.id)

    employees = _dtr_employee_picker_rows(employee_q)
    if my_dtr_mode and viewer_emp:
        selected_employee = viewer_emp
    else:
        picked = _resolve_employee_from_scoped_list(employees, selected_employee_id)
        selected_employee = Employee.query.get(picked.id) if picked else None

    rows = []
    summary = {'lates': 0, 'undertimes': 0, 'absences': 0, 'duplicate_punches': 0}
    monthly_checklist = None
    is_plantilla_viewer = False
    is_jo_cos_viewer = False
    accomplishment_export_allowed = False
    if selected_employee:
        rows, summary, _ = _build_dtr_view_rows(selected_employee, start_d, end_d)
        for r in rows:
            r['can_justify'] = dtr_monthly.can_justify_date_for_employee(selected_employee, r['date'])
        if _is_jo_cos_employee(selected_employee):
            is_jo_cos_viewer = True
            enrich_jo_cos_dtr_rows(selected_employee.id, rows, start_d, end_d)
            if my_dtr_mode:
                accomplishment_export_allowed = has_approved_justification_for_export(rows)
        if my_dtr_mode and viewer_emp and dtr_monthly.is_plantilla_employee(viewer_emp):
            is_plantilla_viewer = True
            if mode == 'month':
                dtr_monthly.maybe_notify_monthly_reminder(viewer_emp, year, month)
                monthly_checklist = dtr_monthly.monthly_checklist(viewer_emp, year, month, rows)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()

    can_submit_justification = dtr_period.can_employee_submit_justification(quincena_period)
    if mode == 'month' and my_dtr_mode and is_plantilla_viewer:
        can_submit_justification = bool(
            monthly_checklist and monthly_checklist.get('window_open')
            and (monthly_checklist.get('submission_status') != dtr_monthly.STATUS_ACCEPTED)
        )
    period_locked = dtr_period.is_period_locked(quincena_period)

    justify_date_allowed = False
    if justify_date_raw and selected_employee:
        try:
            jd = datetime.strptime(justify_date_raw, '%Y-%m-%d').date()
            justify_date_allowed = dtr_monthly.can_justify_date_for_employee(selected_employee, jd)
        except ValueError:
            justify_date_allowed = False

    return render_template(
        'dtr_records.html',
        employee=employee,
        employees=employees,
        selected_employee=selected_employee,
        rows=rows,
        summary=summary,
        mode=mode,
        month=month,
        year=year,
        quincena=quincena,
        start_d=start_d,
        end_d=end_d,
        dtr_edit_mode=dtr_edit_mode,
        my_dtr_mode=my_dtr_mode,
        is_plantilla_viewer=is_plantilla_viewer,
        is_jo_cos_viewer=is_jo_cos_viewer,
        accomplishment_export_allowed=accomplishment_export_allowed,
        quincena_period=quincena_period,
        monthly_checklist=monthly_checklist,
        period_status_label=dtr_period.STATUS_LABELS.get(
            (quincena_period.status if quincena_period else ''), 'Not started'
        ),
        can_submit_justification=can_submit_justification,
        period_locked=period_locked,
        justify_date=justify_date_raw if justify_date_allowed else '',
        justify_date_allowed=justify_date_allowed,
        dtr_justification_reasons=DTR_JUSTIFICATION_REASONS,
        dtr_justification_reason_others=DTR_JUSTIFICATION_REASON_OTHERS,
        can_review_punch_flags=can_review_punch_flags,
    )


@bp.route('/dtr/records/save', methods=['POST'])
@login_required
def dtr_records_save():
    """Admin/HR/Payroll Maker remarks-only save. Punch times cannot be changed here."""
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr', 'payroll_maker') and not _user_is_admin_or_hr():
        flash('Access denied. This section is for Admin, HR, and Payroll Maker.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    try:
        emp_id = int((request.form.get('employee_id') or '0').strip())
    except ValueError:
        flash('Invalid employee.', 'error')
        return redirect(url_for('routes.dtr_records', edit=1))
    emp = Employee.query.filter_by(id=emp_id, status='active').first()
    if not emp:
        flash('Employee not found.', 'error')
        return redirect(url_for('routes.dtr_records', edit=1))
    if role == 'payroll_maker' and not _user_can_access_punch_flag_employee(emp):
        flash('Access denied. Payroll Maker may only edit remarks for employees in their department.', 'error')
        return redirect(url_for('routes.dtr_records', edit=1))

    today = date.today()
    month = int((request.form.get('month') or str(today.month)).strip())
    year = int((request.form.get('year') or str(today.year)).strip())
    mode = (request.form.get('mode') or 'month').strip().lower()
    quincena = (request.form.get('quincena') or '1').strip()
    redir_kw = {'employee_id': emp_id, 'month': month, 'year': year, 'mode': mode, 'quincena': quincena, 'edit': 1}

    if mode == 'quincena':
        start_d, end_d = _quincena_date_range(year, month, quincena)
        period = dtr_period.get_period(year, month, quincena)
        if dtr_period.is_period_locked(period):
            flash('This quincena has been processed. Reopen it from Quincena Processing before adjusting DTR.', 'error')
            return redirect(url_for('routes.dtr_records', **{k: v for k, v in redir_kw.items() if k != 'edit'}))
    else:
        start_d, end_d = _month_date_range(year, month)

    try:
        curr = start_d
        while curr <= end_d:
            dkey = curr.isoformat()
            rem_raw = (request.form.get(f'rem_{dkey}', '') or '').strip()
            rem = (rem_raw[:100] if rem_raw else None)
            rec = DailyTimeRecord.query.filter_by(employee_id=emp.id, record_date=curr).first()

            if not rec:
                if rem_raw:
                    rec = DailyTimeRecord(employee_id=emp.id, record_date=curr, remarks=rem)
                    db.session.add(rec)
                curr += timedelta(days=1)
                continue

            # Remarks only — never mutate punch times or delete rows from this form.
            rec.remarks = rem
            curr += timedelta(days=1)
        db.session.commit()
        flash('DTR remarks updated. Punch times can only be corrected from duplicate-punch review.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving DTR remarks: {str(e)}', 'error')
    return redirect(url_for('routes.dtr_records', **redir_kw))


@bp.route('/attendance', methods=['GET'])
@login_required
def event_attendance_list():
    """HR/Admin: view Flag Raising / Flag Retreat / Meeting attendance."""
    if not _user_is_admin_or_hr():
        flash('Access denied. This section is for Admin and HR.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))

    from app.event_attendance_service import category_label, fill_missing_locations, normalize_category

    today = date.today()
    category = normalize_category(request.args.get('category')) or ''
    q = (request.args.get('q') or '').strip()
    date_from_raw = (request.args.get('date_from') or '').strip()
    date_to_raw = (request.args.get('date_to') or '').strip()
    try:
        date_from = datetime.strptime(date_from_raw, '%Y-%m-%d').date() if date_from_raw else None
    except ValueError:
        date_from = None
    try:
        date_to = datetime.strptime(date_to_raw, '%Y-%m-%d').date() if date_to_raw else None
    except ValueError:
        date_to = None

    query = EventAttendance.query.outerjoin(
        Employee, Employee.id == EventAttendance.employee_id
    )
    if category:
        query = query.filter(EventAttendance.category == category)
    if date_from:
        query = query.filter(EventAttendance.event_date >= date_from)
    if date_to:
        query = query.filter(EventAttendance.event_date <= date_to)
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            EventAttendance.badge_employee_id.ilike(like),
            EventAttendance.location_text.ilike(like),
            EventAttendance.barangay.ilike(like),
            EventAttendance.municipality.ilike(like),
            EventAttendance.province.ilike(like),
            Employee.employee_id.ilike(like),
            Employee.first_name.ilike(like),
            Employee.last_name.ilike(like),
            Employee.middle_name.ilike(like),
        ))

    rows = (
        query
        .options(db.joinedload(EventAttendance.employee).joinedload(Employee.pds))
        .order_by(
            EventAttendance.event_date.desc(),
            EventAttendance.category.asc(),
            EventAttendance.punched_at.asc(),
            EventAttendance.id.asc(),
        )
        .limit(2000)
        .all()
    )
    # Resolve lat/long → barangay, town, province for rows still missing location.
    try:
        fill_missing_locations(rows, max_lookups=25)
    except Exception:
        db.session.rollback()

    recent_batches = (
        EventAttendanceBatch.query
        .order_by(EventAttendanceBatch.uploaded_at.desc())
        .limit(10)
        .all()
    )
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'attendance/event_attendance.html',
        rows=rows,
        recent_batches=recent_batches,
        filters={
            'category': category,
            'q': q,
            'date_from': date_from.isoformat() if date_from else '',
            'date_to': date_to.isoformat() if date_to else '',
        },
        categories=EVENT_ATTENDANCE_CATEGORIES,
        category_labels=EVENT_ATTENDANCE_CATEGORY_LABELS,
        category_label=category_label,
        default_event_date=today.isoformat(),
        employee=employee,
    )


@bp.route('/attendance/upload', methods=['POST'])
@login_required
def event_attendance_upload():
    if not _user_is_admin_or_hr():
        flash('Access denied. This section is for Admin and HR.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))

    from app.event_attendance_service import category_label, normalize_category, upload_event_attendance

    category = (request.form.get('category') or '').strip()
    date_raw = (request.form.get('event_date') or '').strip()
    file = request.files.get('attendance_file')
    try:
        event_date = datetime.strptime(date_raw, '%Y-%m-%d').date()
    except ValueError:
        flash('Valid event date is required.', 'error')
        return redirect(url_for('routes.event_attendance_list'))
    if not file or not file.filename:
        flash('Please choose an Excel (.xlsx) file.', 'error')
        return redirect(url_for('routes.event_attendance_list'))
    fname = secure_filename(file.filename)
    if not fname.lower().endswith(('.xlsx', '.xlsm')):
        flash('Upload an Excel file (.xlsx).', 'error')
        return redirect(url_for('routes.event_attendance_list'))
    try:
        batch = upload_event_attendance(
            file_storage=file,
            category=category,
            event_date=event_date,
            filename=fname,
        )
        flash(
            f'Uploaded {category_label(batch.category)} for {batch.event_date.isoformat()}: '
            f'{batch.inserted_count} added, {batch.skipped_count} duplicate(s) skipped'
            + (f', {batch.unmatched_count} unmatched badge(s)' if batch.unmatched_count else '')
            + '.',
            'success' if batch.inserted_count else 'info',
        )
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'error')
    except Exception as e:
        db.session.rollback()
        flash(f'Upload failed: {e}', 'error')
    return redirect(url_for(
        'routes.event_attendance_list',
        category=normalize_category(category) or '',
        date_from=date_raw,
        date_to=date_raw,
    ))


@bp.route('/dtr/justifications', methods=['GET', 'POST'])
@login_required
def dtr_justifications():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    employee = current_user.employee if current_user.employee else None
    viewer_emp = _current_employee_for_user()

    if request.method == 'POST':
        if not viewer_emp:
            flash('Your account is not linked to an employee.', 'error')
            return redirect(url_for(_dashboard_for_user(current_user)))
        rec_date_raw = (request.form.get('record_date') or '').strip()
        reason, reason_err = _dtr_justification_reason_from_form(request.form)
        if not rec_date_raw or reason_err or not reason:
            flash(reason_err or 'Date and reason are required.', 'error')
            return _redirect_after_dtr_justify_error(role, request.form)
        try:
            rec_date = datetime.strptime(rec_date_raw, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date.', 'error')
            return _redirect_after_dtr_justify_error(role, request.form)
        q_year, q_month = rec_date.year, rec_date.month
        if not dtr_monthly.can_justify_date_for_employee(viewer_emp, rec_date):
            flash('The review period for this date is closed. Contact HR if you need to submit a justification.', 'error')
            return _redirect_after_dtr_justify_error(role, request.form)
        attachment = request.files.get('attachment')
        attachment_name = None
        if attachment and attachment.filename:
            if not _is_allowed_justification_filename(attachment.filename):
                flash('Unsupported attachment type. Allowed: PDF, PNG, JPG, DOC, DOCX.', 'error')
                return _redirect_after_dtr_justify_error(role, request.form)
            safe_name = secure_filename(attachment.filename)
            stamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
            attachment_name = f'just_{viewer_emp.id}_{rec_date.strftime("%Y%m%d")}_{stamp}_{safe_name}'
            attachment.save(os.path.join(_justification_upload_dir(), attachment_name))

        row = DtrJustification.query.filter_by(employee_id=viewer_emp.id, record_date=rec_date).first()
        if not row:
            row = DtrJustification(
                employee_id=viewer_emp.id,
                record_date=rec_date,
                reason=reason,
                status='submitted',
                attachment_name=attachment_name,
                submitted_by_user_id=current_user.id if getattr(current_user, 'id', None) else None,
            )
            db.session.add(row)
        else:
            row.reason = reason
            row.status = 'submitted'
            if attachment_name:
                row.attachment_name = attachment_name
            row.submitted_by_user_id = current_user.id if getattr(current_user, 'id', None) else None
        db.session.commit()
        dtr_period.notify_hr_justification_submitted(row)
        flash('Justification submitted.', 'success')
        if role == 'employee':
            return redirect(url_for('routes.dtr_my_records'))
        return redirect(url_for('routes.dtr_justifications'))

    q = DtrJustification.query.join(Employee, Employee.id == DtrJustification.employee_id)
    if role in ('admin', 'hr'):
        rows = q.order_by(DtrJustification.record_date.desc(), DtrJustification.id.desc()).all()
    elif role == 'payroll_maker':
        if viewer_emp and viewer_emp.department_id:
            rows = (q.filter(Employee.department_id == viewer_emp.department_id)
                    .order_by(DtrJustification.record_date.desc(), DtrJustification.id.desc())
                    .all())
        else:
            rows = []
    else:
        if not viewer_emp:
            rows = []
        else:
            rows = (q.filter(DtrJustification.employee_id == viewer_emp.id)
                    .order_by(DtrJustification.record_date.desc(), DtrJustification.id.desc())
                    .all())
    return render_template('dtr_justifications/list.html', employee=employee, rows=rows)


@bp.route('/dtr/justifications/<int:id>/attachment')
@login_required
def dtr_justification_attachment(id):
    row = DtrJustification.query.get_or_404(id)
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    viewer_emp = _current_employee_for_user()
    if role not in ('admin', 'hr', 'payroll_maker'):
        if not viewer_emp or row.employee_id != viewer_emp.id:
            abort(403)
    if not row.attachment_name:
        flash('No attachment for this justification.', 'warning')
        return redirect(url_for('routes.dtr_justifications'))
    path = os.path.join(_justification_upload_dir(), row.attachment_name)
    if not os.path.exists(path):
        flash('Attachment file not found.', 'error')
        return redirect(url_for('routes.dtr_justifications'))
    return send_file(path, as_attachment=True, download_name=row.attachment_name)


@bp.route('/dtr/justifications/<int:id>/attachment/preview')
@login_required
def dtr_justification_attachment_preview(id):
    row = DtrJustification.query.get_or_404(id)
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    viewer_emp = _current_employee_for_user()
    if role not in ('admin', 'hr', 'payroll_maker'):
        if not viewer_emp or row.employee_id != viewer_emp.id:
            abort(403)
    if not row.attachment_name:
        flash('No attachment for this justification.', 'warning')
        return redirect(url_for('routes.dtr_justifications'))
    path = os.path.join(_justification_upload_dir(), row.attachment_name)
    if not os.path.exists(path):
        flash('Attachment file not found.', 'error')
        return redirect(url_for('routes.dtr_justifications'))
    _, ext = os.path.splitext(row.attachment_name.lower())
    if ext not in ('.pdf', '.png', '.jpg', '.jpeg'):
        flash('Preview available only for PDF/Image attachments.', 'warning')
        return redirect(url_for('routes.dtr_justifications'))
    mimetype = 'application/pdf' if ext == '.pdf' else ('image/png' if ext == '.png' else 'image/jpeg')
    return send_file(path, as_attachment=False, mimetype=mimetype, download_name=row.attachment_name)


@bp.route('/dtr/justifications/<int:id>/forward', methods=['POST'])
@login_required
def dtr_justification_forward(id):
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('payroll_maker', 'admin', 'hr'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    row = DtrJustification.query.get_or_404(id)
    row.status = 'forwarded'
    row.forwarded_by_user_id = current_user.id if getattr(current_user, 'id', None) else None
    db.session.commit()
    flash('Justification forwarded to HR/Admin.', 'success')
    return redirect(url_for('routes.dtr_justifications'))


@bp.route('/dtr/justifications/<int:id>/approve', methods=['POST'])
@login_required
def dtr_justification_approve(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    row = DtrJustification.query.get_or_404(id)
    row.status = 'approved'
    row.reviewed_by_user_id = current_user.id if getattr(current_user, 'id', None) else None
    reverse_lwop_for_approved_justification(
        row, created_by_user_id=getattr(current_user, 'id', None),
    )
    db.session.commit()
    dtr_period.notify_employee_justification_decided(row, approved=True)
    flash('Justification approved.', 'success')
    return redirect(url_for('routes.dtr_justifications'))


@bp.route('/dtr/justifications/<int:id>/reject', methods=['POST'])
@login_required
def dtr_justification_reject(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    row = DtrJustification.query.get_or_404(id)
    row.status = 'rejected'
    row.reviewed_by_user_id = current_user.id if getattr(current_user, 'id', None) else None
    db.session.commit()
    dtr_period.notify_employee_justification_decided(row, approved=False)
    flash('Justification rejected.', 'success')
    return redirect(url_for('routes.dtr_justifications'))


@bp.route('/dtr/monthly-submission', methods=['POST'])
@login_required
def dtr_monthly_submission_submit():
    viewer_emp = _current_employee_for_user()
    if not viewer_emp or not dtr_monthly.is_plantilla_employee(viewer_emp):
        flash('Monthly DTR submission is for plantilla employees only.', 'error')
        return redirect(url_for('routes.dtr_my_records'))
    try:
        year = int((request.form.get('year') or '0').strip())
        month = int((request.form.get('month') or '0').strip())
    except ValueError:
        flash('Invalid period.', 'error')
        return redirect(url_for('routes.dtr_my_records'))
    if month < 1 or month > 12:
        flash('Invalid period.', 'error')
        return redirect(url_for('routes.dtr_my_records'))
    start_d, end_d = _month_date_range(year, month)
    rows, _, _ = _build_dtr_view_rows(viewer_emp, start_d, end_d)
    ok, msg = dtr_monthly.submit_monthly(
        viewer_emp,
        current_user.id if getattr(current_user, 'id', None) else None,
        year,
        month,
        rows,
    )
    if ok:
        db.session.commit()
        flash(msg, 'success')
    else:
        db.session.rollback()
        flash(msg, 'error')
    return redirect(url_for('routes.dtr_my_records', year=year, month=month, mode='month'))


@bp.route('/dtr/monthly-submissions', methods=['GET', 'POST'])
@login_required
def dtr_monthly_submissions():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    today = date.today()
    year = int((request.args.get('year') or request.form.get('year') or str(today.year)).strip())
    month = int((request.args.get('month') or request.form.get('month') or str(today.month)).strip())
    department_id_raw = (request.args.get('department_id') or request.form.get('department_id') or '').strip()
    department_id = None
    if department_id_raw:
        try:
            department_id = int(department_id_raw)
        except ValueError:
            department_id = None

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()
        try:
            sub_id = int((request.form.get('submission_id') or '0').strip())
        except ValueError:
            sub_id = 0
        sub = DtrMonthlySubmission.query.get(sub_id)
        if not sub:
            flash('Submission not found.', 'error')
        elif action == 'accept':
            notes = (request.form.get('review_notes') or '').strip()
            dtr_monthly.accept_submission(sub, getattr(current_user, 'id', None), notes)
            db.session.commit()
            flash('Monthly DTR accepted.', 'success')
        elif action == 'reject':
            notes = (request.form.get('review_notes') or '').strip()
            if not notes:
                flash('Review notes are required when rejecting a monthly submission.', 'error')
                return redirect(url_for(
                    'routes.dtr_monthly_submissions',
                    year=year, month=month, department_id=department_id or '',
                ))
            dtr_monthly.reject_submission(sub, getattr(current_user, 'id', None), notes)
            db.session.commit()
            flash('Monthly DTR returned to employee for resubmission.', 'success')
        return redirect(url_for(
            'routes.dtr_monthly_submissions',
            year=year, month=month, department_id=department_id or '',
        ))

    submissions = dtr_monthly.plantilla_submissions_for_month(year, month, department_id)
    departments = Department.query.order_by(Department.name.asc()).all()
    pending_count = sum(1 for s in submissions if s['status'] in (dtr_monthly.STATUS_PENDING, dtr_monthly.STATUS_REJECTED))
    submitted_count = sum(1 for s in submissions if s['status'] == dtr_monthly.STATUS_SUBMITTED)
    accepted_count = sum(1 for s in submissions if s['status'] == dtr_monthly.STATUS_ACCEPTED)
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'dtr_monthly_submissions.html',
        employee=employee,
        submissions=submissions,
        year=year,
        month=month,
        department_id=department_id_raw,
        departments=departments,
        period_label=dtr_monthly.monthly_period_label(year, month),
        deadline=dtr_monthly.submission_deadline(year, month),
        window_open=dtr_monthly.is_monthly_window_open(year, month),
        pending_count=pending_count,
        submitted_count=submitted_count,
        accepted_count=accepted_count,
    )


@bp.route('/dtr/my')
@login_required
def dtr_my_records():
    viewer_emp = _current_employee_for_user()
    if not viewer_emp:
        flash('Employee record not linked to your account.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    today = date.today()
    year = int((request.args.get('year') or str(today.year)).strip())
    month = int((request.args.get('month') or str(today.month)).strip())
    quincena = (request.args.get('quincena') or ('1' if today.day <= 15 else '2')).strip()
    justify_date = (request.args.get('justify_date') or '').strip()
    mode = (request.args.get('mode') or 'quincena').strip().lower()
    if mode not in ('quincena', 'month'):
        mode = 'quincena'
    if not dtr_monthly.is_plantilla_employee(viewer_emp):
        mode = 'quincena'
    return redirect(url_for(
        'routes.dtr_records',
        my=1,
        employee_id=viewer_emp.id,
        mode=mode,
        year=year,
        month=month,
        quincena=quincena,
        justify_date=justify_date or None,
    ))


@bp.route('/dtr/records/print')
@login_required
def dtr_records_print():
    viewer_emp = _current_employee_for_user()
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    today = date.today()
    month = int((request.args.get('month') or str(today.month)).strip())
    year = int((request.args.get('year') or str(today.year)).strip())
    quincena = (request.args.get('quincena') or '1').strip()
    mode = (request.args.get('mode') or 'quincena').strip().lower()
    emp_id_raw = (request.args.get('employee_id') or '').strip()
    my_only = (request.args.get('my') or '').strip() == '1'

    if mode == 'quincena':
        start_d, end_d = _quincena_date_range(year, month, quincena)
    else:
        start_d, end_d = _month_date_range(year, month)

    if my_only or role == 'employee':
        if not viewer_emp:
            abort(403)
        emp = viewer_emp
    elif role in ('admin', 'hr', 'payroll_maker', 'manager'):
        try:
            emp_id = int(emp_id_raw)
        except ValueError:
            flash('Select an employee to print.', 'error')
            return redirect(url_for('routes.dtr_records'))
        emp = Employee.query.get_or_404(emp_id)
        if role == 'payroll_maker' and not _user_can_access_punch_flag_employee(emp):
            flash('Access denied. Payroll Maker may only print DTR for employees in their department.', 'error')
            return redirect(url_for('routes.dtr_records'))
        if role == 'manager':
            dept_ids = _managed_department_ids_for_manager()
            if not emp.department_id or emp.department_id not in dept_ids:
                flash('Access denied. You may only print DTR for employees in your managed department(s).', 'error')
                return redirect(url_for('routes.dtr_records'))
    else:
        abort(403)

    rows, _, _ = _build_dtr_view_rows(emp, start_d, end_d)
    if my_only and dtr_monthly.is_plantilla_employee(emp) and mode == 'month':
        dtr_monthly.mark_printed(emp, year, month)
    if _is_jo_cos_employee(emp):
        enrich_jo_cos_dtr_rows(emp.id, rows, start_d, end_d)
        buf = _dtr_csc_form48_pdf_bytes(
            emp, rows, start_d=start_d, end_d=end_d, remarks_style='jo_cos',
        )
    else:
        buf = _dtr_csc_form48_pdf_bytes(
            emp, rows, start_d=start_d, end_d=end_d, remarks_style='plantilla',
        )
    fname = f'dtr_{emp.employee_id}_{year}_{month:02d}'
    if mode == 'quincena':
        fname += f'_Q{quincena}'
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{fname}.pdf')


@bp.route('/dtr/my/accomplishment-reports', methods=['POST'])
@login_required
def dtr_accomplishment_reports_save():
    viewer_emp = _current_employee_for_user()
    if not viewer_emp or not _is_jo_cos_employee(viewer_emp):
        flash('Accomplishment reports are for JO/COS employees only.', 'error')
        return redirect(url_for('routes.dtr_my_records'))
    today = date.today()
    month = int((request.form.get('month') or str(today.month)).strip())
    year = int((request.form.get('year') or str(today.year)).strip())
    quincena = (request.form.get('quincena') or '1').strip()
    mode = (request.form.get('mode') or 'quincena').strip().lower()
    if mode == 'quincena':
        start_d, end_d = _quincena_date_range(year, month, quincena)
    else:
        start_d, end_d = _month_date_range(year, month)

    reports_by_date: dict[date, str] = {}
    prefix = 'accomplishment_'
    for key, val in request.form.items():
        if not key.startswith(prefix):
            continue
        raw_date = key[len(prefix):]
        try:
            record_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            continue
        if start_d <= record_date <= end_d:
            reports_by_date[record_date] = val

    try:
        count = save_accomplishment_reports(
            viewer_emp.id,
            reports_by_date,
            updated_by_user_id=getattr(current_user, 'id', None),
        )
        db.session.commit()
        flash('Accomplishment report(s) saved.', 'success')
    except Exception as exc:
        db.session.rollback()
        flash(f'Could not save accomplishment reports: {exc}', 'error')

    return redirect(url_for(
        'routes.dtr_my_records',
        year=year,
        month=month,
        mode=mode,
        quincena=quincena,
    ))


@bp.route('/dtr/records/accomplishment-pdf')
@login_required
def dtr_accomplishment_pdf():
    viewer_emp = _current_employee_for_user()
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    today = date.today()
    month = int((request.args.get('month') or str(today.month)).strip())
    year = int((request.args.get('year') or str(today.year)).strip())
    quincena = (request.args.get('quincena') or '1').strip()
    mode = (request.args.get('mode') or 'quincena').strip().lower()
    inline = (request.args.get('inline') or '').strip() == '1'

    if mode == 'quincena':
        start_d, end_d = _quincena_date_range(year, month, quincena)
        period_label = dtr_period.quincena_period_label(year, month, quincena)
    else:
        start_d, end_d = _month_date_range(year, month)
        period_label = date(year, month, 1).strftime('%B %Y')

    my_only = (request.args.get('my') or '').strip() == '1'
    if my_only or role == 'employee':
        if not viewer_emp:
            abort(403)
        emp = viewer_emp
    elif role in ('admin', 'hr', 'payroll_maker'):
        try:
            emp_id = int((request.args.get('employee_id') or '').strip())
        except ValueError:
            flash('Select an employee.', 'error')
            return redirect(url_for('routes.dtr_records'))
        emp = Employee.query.get_or_404(emp_id)
    else:
        abort(403)

    if not _is_jo_cos_employee(emp):
        flash('Accomplishment reports apply to JO/COS employees only.', 'error')
        return redirect(url_for('routes.dtr_my_records'))

    rows, _, _ = _build_dtr_view_rows(emp, start_d, end_d)
    enrich_jo_cos_dtr_rows(emp.id, rows, start_d, end_d)
    export_rows = approved_accomplishment_export_rows(emp.id, rows)
    if not export_rows:
        flash('No approved justifications in this period to export.', 'warning')
        return redirect(url_for(
            'routes.dtr_my_records',
            year=year,
            month=month,
            mode=mode,
            quincena=quincena,
        ))

    buf = _dtr_accomplishment_pdf_bytes(emp, export_rows, period_label)
    fname = f'accomplishment_{emp.employee_id}_{year}_{month:02d}'
    if mode == 'quincena':
        fname += f'_Q{quincena}'
    return send_file(
        buf,
        mimetype='application/pdf',
        as_attachment=not inline,
        download_name=f'{fname}.pdf',
    )


@bp.route('/dtr/quincena-processing', methods=['GET', 'POST'])
@login_required
def dtr_quincena_processing():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    anchor = date.today()
    opts = _build_quincena_upload_options(anchor)
    default_period = f'{anchor.year:04d}-{anchor.month:02d}-{"1" if anchor.day <= 15 else "2"}'
    selected = (request.args.get('period') or request.form.get('period') or default_period).strip()
    year, month, quincena = _parse_quincena_upload_value(selected)
    if year is None:
        year, month, quincena = _parse_quincena_upload_value(default_period)

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()
        py, pm, pq = _parse_quincena_upload_value((request.form.get('period') or '').strip())
        if py is None:
            flash('Invalid quincena.', 'error')
            return redirect(url_for('routes.dtr_quincena_processing'))
        period = dtr_period.get_or_create_period(py, pm, pq)
        if action == 'open_review':
            deadline_raw = (request.form.get('review_deadline') or '').strip()
            deadline = None
            if deadline_raw:
                try:
                    deadline = datetime.strptime(deadline_raw, '%Y-%m-%d').date()
                except ValueError:
                    flash('Invalid review deadline.', 'error')
                    return redirect(url_for('routes.dtr_quincena_processing', period=request.form.get('period')))
            dtr_period.open_review(period, deadline)
            db.session.commit()
            flash('Employee review period opened.', 'success')
        elif action == 'close_review':
            dtr_period.close_review(period)
            db.session.commit()
            flash('Employee review period closed. You may adjust DTR and process worktime.', 'success')
        elif action == 'reopen':
            dtr_period.reopen_for_adjustment(period)
            db.session.commit()
            flash('Quincena reopened for DTR adjustment.', 'success')
        return redirect(url_for('routes.dtr_quincena_processing', period=request.form.get('period')))

    period = dtr_period.get_or_create_period(year, month, quincena)
    stats = dtr_period.period_dashboard_stats(year, month, quincena)
    period_label = dtr_period.quincena_period_label(year, month, quincena)
    status = (period.status or dtr_period.STATUS_DRAFT).strip()
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'dtr_quincena_processing.html',
        quincena_options=opts,
        selected_period=selected,
        period=period,
        period_label=period_label,
        period_status=status,
        period_status_label=dtr_period.STATUS_LABELS.get(status, status.title()),
        stats=stats,
        employee=employee,
        is_locked=dtr_period.is_period_locked(period),
        can_close_review=status == dtr_period.STATUS_REVIEW_OPEN,
        can_open_review=status in (dtr_period.STATUS_DRAFT, dtr_period.STATUS_UPLOADED, dtr_period.STATUS_REVIEW_CLOSED),
        can_process=status in (dtr_period.STATUS_REVIEW_CLOSED, dtr_period.STATUS_REVIEW_OPEN, dtr_period.STATUS_UPLOADED),
    )


_DTR_REGEN_REMARKS_PREFIX = 'DTR_REGEN:'


def _dtr_regen_tag(year: int, month: int, quincena: str) -> str:
    return f'{_DTR_REGEN_REMARKS_PREFIX}{year:04d}-{month:02d}-Q{quincena}'


def _mins_to_hhmm(total_mins: int) -> str:
    if total_mins < 0:
        total_mins = 0
    return f'{total_mins // 60:02d}:{total_mins % 60:02d}'


def _worktime_summary_parse_filters(args) -> dict:
    today = date.today()
    try:
        year = int((args.get('year') or str(today.year)).strip())
    except ValueError:
        year = today.year
    try:
        month = int((args.get('month') or str(today.month)).strip())
    except ValueError:
        month = today.month
    quincena = (args.get('quincena') or ('1' if today.day <= 15 else '2')).strip()
    if quincena not in ('1', '2'):
        quincena = '1'
    if month < 1 or month > 12:
        month = today.month
    if year < 2000 or year > 2100:
        year = today.year
    employee_type = (args.get('employee_type') or 'plantilla').strip().lower()
    if employee_type not in ('plantilla', 'jo_cos'):
        employee_type = 'plantilla'
    return {
        'year': year,
        'month': month,
        'quincena': quincena,
        'employee_type': employee_type,
        'employee_id': (args.get('employee_id') or '').strip(),
        'department_id': (args.get('department_id') or '').strip(),
    }


def _worktime_summary_eligible_employees(filters: dict) -> list[Employee]:
    """Active employees matching type (and optional department / single-employee filters)."""
    q = Employee.query.filter_by(status='active')
    if filters['department_id']:
        try:
            q = q.filter(Employee.department_id == int(filters['department_id']))
        except ValueError:
            pass
    if filters['employee_id']:
        try:
            q = q.filter(Employee.id == int(filters['employee_id']))
        except ValueError:
            pass
    employees = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()
    if filters.get('employee_type') == 'jo_cos':
        return [e for e in employees if _is_jo_cos_employee(e)]
    return [e for e in employees if not _is_jo_cos_employee(e)]


def _worktime_summary_filter_label(filters: dict, dept_by_id: dict[int, Department]) -> str:
    parts = [
        f"{date(filters['year'], filters['month'], 1).strftime('%B %Y')} — "
        f"{'1st' if filters['quincena'] == '1' else '2nd'} quincena"
    ]
    type_label = 'JO/COS' if filters.get('employee_type') == 'jo_cos' else 'Plantilla'
    parts.append(type_label)
    if filters['department_id']:
        try:
            dept = dept_by_id.get(int(filters['department_id']))
            parts.append(f"Department: {dept.name if dept else filters['department_id']}")
        except ValueError:
            pass
    if filters['employee_id']:
        try:
            emp = Employee.query.get(int(filters['employee_id']))
            if emp:
                parts.append(f"Employee: {emp.directory_name}")
        except ValueError:
            pass
    return ' · '.join(parts)


def _worktime_summary_query(filters: dict):
    eligible_ids = [e.id for e in _worktime_summary_eligible_employees(filters)]
    q = (
        WorkHours.query
        .join(Employee, WorkHours.employee_id == Employee.id)
        .outerjoin(Department, WorkHours.department_id == Department.id)
        .filter(
            WorkHours.year == filters['year'],
            WorkHours.month == filters['month'],
            WorkHours.quincena_half == filters['quincena'],
        )
    )
    if eligible_ids:
        q = q.filter(WorkHours.employee_id.in_(eligible_ids))
    else:
        q = q.filter(WorkHours.employee_id == -1)
    return q.order_by(
        WorkHours.work_date.asc(),
        Employee.last_name.asc(),
        Employee.first_name.asc(),
        WorkHours.id.asc(),
    )


def _worktime_summary_view_mode(filters: dict) -> str:
    """daily = per calendar day (employee filter); by_employee = quincena totals per employee."""
    return 'daily' if filters.get('employee_id') else 'by_employee'


WORKTIME_SUMMARY_DAY_MINS = DTR_EIGHT_HOUR_DAY_MINS  # 10 × 8h = 80h quincena equivalent


def _worktime_summary_days_worked_display(day_equiv: Decimal) -> str:
    """Equivalent days worked on an 8-hour basis (rounded to nearest whole day)."""
    if day_equiv <= 0:
        return '0'
    days = int(day_equiv.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return str(days)


def _worktime_summary_creditable_mins(wh: WorkHours) -> int:
    """
    Minutes that count toward days worked: net rendered, or gross when higher.
    Gross includes present days, approved leave, holidays, and work suspensions
    saved during DTR regeneration.
    """
    net_mins = int(wh.net_rendered_mins or 0)
    gross_mins = int(wh.gross_work_mins or 0)
    return max(net_mins, gross_mins)


def _worktime_summary_days_from_creditable_mins(creditable_mins: int) -> str:
    """Convert creditable minutes to equivalent 8-hour days (e.g. 4800 mins → 10)."""
    if creditable_mins <= 0:
        return '0'
    day_equiv = Decimal(str(creditable_mins)) / Decimal(str(WORKTIME_SUMMARY_DAY_MINS))
    return _worktime_summary_days_worked_display(day_equiv)


def _worktime_summary_daily_row_dict(wh: WorkHours) -> dict:
    emp = wh.employee
    dept = wh.department
    code = (emp.employee_id or '') if emp else ''
    name = emp.directory_name if emp else ''
    dept_name = dept.name if dept else ''
    remarks = (wh.remarks or '').strip()
    return {
        'work_date': wh.work_date.strftime('%Y-%m-%d'),
        'employee_code': code,
        'employee_name': name,
        'department_name': dept_name,
        'year': wh.year,
        'month': wh.month,
        'quincena_half': wh.quincena_half,
        'day_count': _worktime_summary_days_from_creditable_mins(_worktime_summary_creditable_mins(wh)),
        'gross_work': _mins_to_hhmm(wh.gross_work_mins),
        'late': _mins_to_hhmm(wh.late_mins),
        'undertime': _mins_to_hhmm(wh.undertime_mins),
        'absence': _mins_to_hhmm(wh.absence_mins),
        'net_rendered': _mins_to_hhmm(wh.net_rendered_mins),
        'remarks': remarks,
        'net_rendered_mins': int(wh.net_rendered_mins or 0),
        'search_text': (
            f"{wh.work_date} {code} {name} {dept_name} {remarks}"
        ).lower(),
    }


def _worktime_summary_by_employee_rows(filters: dict) -> list[dict]:
    """Aggregate work_hours into one total per eligible employee for the filtered quincena."""
    eligible = _worktime_summary_eligible_employees(filters)
    buckets: dict[int, dict] = {}
    for emp in eligible:
        dept = emp.department
        code = emp.employee_id or ''
        name = emp.directory_name
        dept_name = dept.name if dept else ''
        buckets[emp.id] = {
            'employee_code': code,
            'employee_name': name,
            'department_name': dept_name,
            'year': filters['year'],
            'month': filters['month'],
            'quincena_half': filters['quincena'],
            'creditable_mins': 0,
            'gross_mins': 0,
            'late_mins': 0,
            'undertime_mins': 0,
            'absence_mins': 0,
            'net_mins': 0,
            'search_text': f"{code} {name} {dept_name}".lower(),
        }

    for wh in _worktime_summary_query(filters).all():
        bucket = buckets.get(wh.employee_id)
        if not bucket:
            continue
        bucket['gross_mins'] += int(wh.gross_work_mins or 0)
        bucket['late_mins'] += int(wh.late_mins or 0)
        bucket['undertime_mins'] += int(wh.undertime_mins or 0)
        bucket['absence_mins'] += int(wh.absence_mins or 0)
        bucket['net_mins'] += int(wh.net_rendered_mins or 0)
        bucket['creditable_mins'] += _worktime_summary_creditable_mins(wh)

    rows = []
    for bucket in buckets.values():
        rows.append({
            'work_date': '',
            'employee_code': bucket['employee_code'],
            'employee_name': bucket['employee_name'],
            'department_name': bucket['department_name'],
            'year': bucket['year'],
            'month': bucket['month'],
            'quincena_half': bucket['quincena_half'],
            'day_count': _worktime_summary_days_from_creditable_mins(bucket['creditable_mins']),
            'gross_work': _mins_to_hhmm(bucket['gross_mins']),
            'late': _mins_to_hhmm(bucket['late_mins']),
            'undertime': _mins_to_hhmm(bucket['undertime_mins']),
            'absence': _mins_to_hhmm(bucket['absence_mins']),
            'net_rendered': _mins_to_hhmm(bucket['net_mins']),
            'remarks': '',
            'net_rendered_mins': bucket['net_mins'],
            'search_text': bucket['search_text'],
        })
    rows.sort(key=lambda r: (r.get('employee_name') or '').lower())
    return rows


def _worktime_summary_result(filters: dict) -> dict:
    view_mode = _worktime_summary_view_mode(filters)
    if view_mode == 'daily':
        rows = [_worktime_summary_daily_row_dict(wh) for wh in _worktime_summary_query(filters).all()]
    else:
        rows = _worktime_summary_by_employee_rows(filters)
    total_net_mins = sum(int(r.get('net_rendered_mins') or 0) for r in rows)
    return {
        'view_mode': view_mode,
        'rows': rows,
        'total_net_mins': total_net_mins,
    }


def _worktime_summary_rows_from_filters(filters: dict) -> list[dict]:
    return _worktime_summary_result(filters)['rows']


def _worktime_summary_csv_bytes(rows: list[dict], period_label: str, view_mode: str) -> bytes:
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['Worktime Summary', period_label])
    w.writerow([f'View: {"Per day" if view_mode == "daily" else "Per employee (quincena total)"}'])
    w.writerow([])
    if view_mode == 'daily':
        w.writerow([
            'Date', 'Employee ID', 'Employee Name', 'Department', 'Year', 'Month', 'Quincena',
            'Gross (HH:MM)', 'Late (HH:MM)', 'Undertime (HH:MM)', 'Absence (HH:MM)', 'Net (HH:MM)', 'Remarks',
        ])
        for r in rows:
            w.writerow([
                r.get('work_date', ''),
                r.get('employee_code', ''),
                r.get('employee_name', ''),
                r.get('department_name', ''),
                r.get('year', ''),
                r.get('month', ''),
                r.get('quincena_half', ''),
                r.get('gross_work', ''),
                r.get('late', ''),
                r.get('undertime', ''),
                r.get('absence', ''),
                r.get('net_rendered', ''),
                r.get('remarks', ''),
            ])
    else:
        w.writerow([
            'Employee ID', 'Employee Name', 'Department', 'Days worked', 'Year', 'Month', 'Quincena',
            'Gross (HH:MM)', 'Late (HH:MM)', 'Undertime (HH:MM)', 'Absence (HH:MM)', 'Net (HH:MM)',
        ])
        for r in rows:
            w.writerow([
                r.get('employee_code', ''),
                r.get('employee_name', ''),
                r.get('department_name', ''),
                r.get('day_count', ''),
                r.get('year', ''),
                r.get('month', ''),
                r.get('quincena_half', ''),
                r.get('gross_work', ''),
                r.get('late', ''),
                r.get('undertime', ''),
                r.get('absence', ''),
                r.get('net_rendered', ''),
            ])
    return output.getvalue().encode('utf-8')


def _worktime_summary_pdf_bytes(rows: list[dict], period_label: str, view_mode: str) -> io.BytesIO:
    """Placeholder PDF layout; formal design to follow."""
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    width, height = landscape(letter)
    y = height - 40
    title = (
        'Worktime Summary (per day)'
        if view_mode == 'daily'
        else 'Worktime Summary (per employee)'
    )
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, y, title)
    y -= 16
    c.setFont('Helvetica', 9)
    c.drawString(40, y, period_label)
    y -= 14
    c.setFont('Helvetica-Oblique', 8)
    c.drawString(40, y, 'PDF layout is provisional and may be revised.')
    y -= 16
    c.setFont('Helvetica-Bold', 7)
    if view_mode == 'daily':
        headers = ['Date', 'Emp ID', 'Name', 'Dept', 'Gross', 'Late', 'UT', 'Abs', 'Net', 'Remarks']
        xs = [40, 95, 145, 285, 355, 395, 430, 465, 500, 540]
    else:
        headers = ['Emp ID', 'Name', 'Dept', 'Days', 'Gross', 'Late', 'UT', 'Abs', 'Net']
        xs = [40, 95, 200, 320, 380, 420, 455, 490, 525]
    for x, h in zip(xs, headers):
        c.drawString(x, y, h)
    y -= 10
    c.setFont('Helvetica', 7)
    for r in rows:
        if y < 40:
            c.showPage()
            y = height - 40
            c.setFont('Helvetica', 7)
        if view_mode == 'daily':
            c.drawString(40, y, str(r.get('work_date', ''))[:10])
            c.drawString(95, y, str(r.get('employee_code', ''))[:10])
            c.drawString(145, y, str(r.get('employee_name', ''))[:24])
            c.drawString(285, y, str(r.get('department_name', ''))[:14])
            c.drawString(355, y, str(r.get('gross_work', '')))
            c.drawString(395, y, str(r.get('late', '')))
            c.drawString(430, y, str(r.get('undertime', '')))
            c.drawString(465, y, str(r.get('absence', '')))
            c.drawString(500, y, str(r.get('net_rendered', '')))
            c.drawString(540, y, str(r.get('remarks', ''))[:28])
        else:
            c.drawString(40, y, str(r.get('employee_code', ''))[:10])
            c.drawString(95, y, str(r.get('employee_name', ''))[:28])
            c.drawString(200, y, str(r.get('department_name', ''))[:18])
            c.drawString(320, y, str(r.get('day_count', '')))
            c.drawString(380, y, str(r.get('gross_work', '')))
            c.drawString(420, y, str(r.get('late', '')))
            c.drawString(455, y, str(r.get('undertime', '')))
            c.drawString(490, y, str(r.get('absence', '')))
            c.drawString(525, y, str(r.get('net_rendered', '')))
        y -= 9
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


JO_COS_PAYROLL_DAY_MINS = 480  # default 8-hour day for JO/COS daily wage conversion


def _jo_cos_payroll_day_mins_for_employee(
    emp_id: int, year: int, month: int, quincena: str, start_d: date, end_d: date
) -> int:
    """Shift length in minutes for converting gross work to paid days (flexi schedule when assigned)."""
    if not _employee_on_flexible_worktime_quincena(emp_id, year, month, quincena):
        return JO_COS_PAYROLL_DAY_MINS
    sched_map = _flexible_worktime_schedule_by_date(emp_id, year, month, quincena, start_d, end_d)
    if not sched_map:
        return JO_COS_PAYROLL_DAY_MINS
    mins = [_scheduled_shift_minutes_from_duty_schedule(s) for s in sched_map.values()]
    return max(1, int(round(sum(mins) / len(mins))))


def _jo_cos_payroll_days_from_gross_mins(gross_mins: int, day_mins: int) -> tuple[Decimal, str]:
    """Whole paid days from gross minutes (rounded half-up)."""
    if gross_mins <= 0 or day_mins <= 0:
        return Decimal('0'), '0'
    day_equiv = Decimal(str(gross_mins)) / Decimal(str(day_mins))
    days = int(day_equiv.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return Decimal(str(days)), str(days)


def _money_decimal(val) -> Decimal:
    try:
        return Decimal(str(val or 0)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal('0.00')


def _money_str(val) -> str:
    d = _money_decimal(val)
    return f'{d:,.2f}'


def _money_str_or_blank(val) -> str:
    d = _money_decimal(val)
    if d == 0:
        return ''
    return f'{d:,.2f}'


def _gsis_contribution_amounts(basic_salary) -> dict:
    """Compute GSIS contribution amounts from monthly basic salary."""
    basic_d = _money_decimal(basic_salary)
    ps = (basic_d * Decimal('0.09')).quantize(Decimal('0.01'))
    gs = (basic_d * Decimal('0.12')).quantize(Decimal('0.01'))
    month_amount = ps
    return {
        'basic_salary': basic_d,
        'ps_amount': ps,
        'gs_amount': gs,
        'month_amount': month_amount,
        'total_amount': month_amount,
        'deducted_amount': month_amount,
    }


GSIS_CONTRIBUTION_DEDUCTIBLE_QUINCENA = '2'

PHIC_CONTRIBUTION_DEDUCTIBLE_QUINCENA = '2'


def _phic_total_premium_from_salary(basic_salary) -> Decimal:
    """
    PhilHealth total monthly premium (EE + ER).

    Tier (2024+ rate table used in this module):
      basic < 10,000      → ₱500.00 total (₱250 each)
      10,000–100,000     → 5% of basic
      basic > 100,000     → ₱5,000.00 total (₱2,500 each)
    """
    basic_d = _money_decimal(basic_salary)
    if basic_d < Decimal('10000'):
        return Decimal('500.00')
    if basic_d <= Decimal('100000'):
        return (basic_d * Decimal('0.05')).quantize(Decimal('0.01'))
    return Decimal('5000.00')


def _phic_ps_gs_split(total_premium) -> tuple[Decimal, Decimal]:
    """
    Equal EE/ER split of PhilHealth premium.

    Same accounting convention as loan/quincena odd-cent splits: the floor half
    goes to PS; any leftover centavo goes to GS.
    Example: total 1,708.85 → PS 854.42, GS 854.43.
    """
    total = _money_decimal(total_premium)
    if total <= 0:
        return Decimal('0.00'), Decimal('0.00')
    ps = (total / Decimal('2')).quantize(Decimal('0.01'), rounding=ROUND_FLOOR)
    gs = (total - ps).quantize(Decimal('0.01'))
    return ps, gs


def _phic_share_from_salary(basic_salary) -> Decimal:
    """Employee (PS) share from monthly basic salary."""
    ps, _gs = _phic_ps_gs_split(_phic_total_premium_from_salary(basic_salary))
    return ps


def _phic_contribution_amounts(basic_salary) -> dict:
    """Compute PHIC contribution amounts from monthly basic salary."""
    basic_d = _money_decimal(basic_salary)
    total = _phic_total_premium_from_salary(basic_d)
    ps, gs = _phic_ps_gs_split(total)
    month_amount = ps
    return {
        'basic_salary': basic_d,
        'ps_amount': ps,
        'gs_amount': gs,
        'month_amount': month_amount,
        'total_amount': month_amount,
        'deducted_amount': month_amount,
    }


def _phic_contribution_row_dict(emp: Employee) -> dict:
    """Build display + raw payload for one plantilla employee PHIC row."""
    basic = _current_salary_amount(emp) or 0.0
    amounts = _phic_contribution_amounts(basic)
    return {
        'employee_pk': emp.id,
        'employee_id': emp.employee_id,
        'employee_name': emp.directory_name,
        'department_name': (emp.department.name if emp.department else ''),
        'basic_salary': _money_str(amounts['basic_salary']),
        'ps': _money_str(amounts['ps_amount']),
        'gs': _money_str(amounts['gs_amount']),
        'month_amount': _money_str(amounts['month_amount']),
        'ps_raw': str(amounts['ps_amount']),
        'gs_raw': str(amounts['gs_amount']),
        'month_amount_raw': str(amounts['month_amount']),
    }


GSIS_LOAN_TYPE_FIELDS = (
    ('consoloan', 'CONSOLOAN'),
    ('emrgyln', 'EMRGYLN'),
    ('plreg', 'PLREG'),
    ('gfal', 'GFAL'),
    ('mpl', 'MPL'),
    ('cpl', 'CPL'),
    ('mpl_lite', 'MPL_LITE'),
)

GSIS_LOAN_UPLOAD_COLUMNS = (
    'BPNO', 'PS', 'GS', 'EC', 'CONSOLOAN', 'EMRGYLN', 'PLREG', 'GFAL', 'MPL', 'CPL', 'MPL_LITE',
)


def _normalize_gsis_bpno(val) -> str:
    s = re.sub(r'\D', '', str(val or '').strip())
    return s or ''


def _gsis_loan_quincena_split(month_amount) -> tuple[Decimal, Decimal]:
    """Split month amount: Q2 gets floor half; Q1 gets remainder (cent goes to Q1)."""
    month_d = _money_decimal(month_amount)
    if month_d <= 0:
        return Decimal('0.00'), Decimal('0.00')
    q2 = (month_d / Decimal('2')).quantize(Decimal('0.01'), rounding=ROUND_FLOOR)
    q1 = (month_d - q2).quantize(Decimal('0.01'))
    return q1, q2


def _gsis_loan_employee_by_bpno() -> dict[str, Employee]:
    """Map normalized BPNO/UMID to active plantilla employees."""
    out: dict[str, Employee] = {}
    rows = (Employee.query
            .options(db.joinedload(Employee.pds), db.joinedload(Employee.department))
            .filter_by(status='active')
            .all())
    for emp in rows:
        if _is_jo_cos_employee(emp):
            continue
        pds = emp.pds
        if not pds or not (pds.umid_id or '').strip():
            continue
        key = _normalize_gsis_bpno(pds.umid_id)
        if key:
            out[key] = emp
    return out


def _parse_gsis_loan_xls(file_bytes: bytes) -> dict:
    import pandas as pd

    df = pd.read_excel(io.BytesIO(file_bytes), engine='xlrd', header=None)
    year, month = date.today().year, date.today().month
    for i in range(min(6, len(df))):
        label = str(df.iloc[i, 0] or '').strip().lower()
        if label == 'due month':
            raw = str(df.iloc[i, 1] or '').strip()
            m = re.match(r'^(\d{1,2})\s*/\s*(\d{4})$', raw)
            if m:
                month, year = int(m.group(1)), int(m.group(2))

    header_row = None
    col_index: dict[str, int] = {}
    for i in range(min(20, len(df))):
        row_vals = [str(x).strip().upper() for x in df.iloc[i].tolist()]
        if 'BPNO' in row_vals:
            header_row = i
            for j, val in enumerate(row_vals):
                if val:
                    col_index[val.replace(' ', '_')] = j
            break
    if header_row is None:
        raise ValueError('Could not find BPNO header row in the GSIS file.')

    def _col(name: str) -> int | None:
        return col_index.get(name.replace(' ', '_').upper())

    bpno_idx = _col('BPNO')
    if bpno_idx is None:
        raise ValueError('BPNO column not found in the GSIS file.')

    amount_cols = {
        'ps_amount': _col('PS'),
        'gs_amount': _col('GS'),
        'ec_amount': _col('EC'),
        'consoloan': _col('CONSOLOAN'),
        'emrgyln': _col('EMRGYLN'),
        'plreg': _col('PLREG'),
        'gfal': _col('GFAL'),
        'mpl': _col('MPL'),
        'cpl': _col('CPL'),
        'mpl_lite': _col('MPL_LITE'),
    }

    emp_by_bpno = _gsis_loan_employee_by_bpno()
    parsed_rows = []
    unmatched = 0
    for i in range(header_row + 1, len(df)):
        bpno_raw = df.iloc[i, bpno_idx]
        if pd.isna(bpno_raw) or str(bpno_raw).strip() == '':
            continue
        bpno = _normalize_gsis_bpno(bpno_raw)
        emp = emp_by_bpno.get(bpno)
        amounts = {}
        for field, idx in amount_cols.items():
            val = Decimal('0.00')
            if idx is not None:
                cell = df.iloc[i, idx]
                if not pd.isna(cell):
                    try:
                        val = _money_decimal(cell)
                    except Exception:
                        val = Decimal('0.00')
            amounts[field] = val
        row = {
            'bpno': bpno,
            'employee_pk': emp.id if emp else None,
            'employee_id': emp.employee_id if emp else '',
            'employee_name': emp.directory_name if emp else '',
            'department_name': (emp.department.name if emp and emp.department else ''),
            'matched': emp is not None,
            **{k: str(v) for k, v in amounts.items()},
            **{f'{k}_display': _money_str_or_blank(v) for k, v in amounts.items()},
        }
        if not emp:
            unmatched += 1
        parsed_rows.append(row)

    parsed_rows.sort(key=lambda r: (r.get('employee_name') or 'zzz', r.get('bpno') or ''))
    return {
        'year': year,
        'month': month,
        'rows': parsed_rows,
        'matched_count': sum(1 for r in parsed_rows if r.get('matched')),
        'unmatched_count': unmatched,
        'total_count': len(parsed_rows),
    }


def _gsis_active_tab() -> str:
    """Return 'contribution' or 'loan' for GSIS page tab state."""
    args = request.args
    if (args.get('generate') or '').strip() == '1':
        return 'contribution'
    if (args.get('loan_load') or '').strip() == '1':
        return 'loan'
    loan_view = (args.get('loan_view') or '').strip().lower()
    if loan_view in ('load', 'upload'):
        return 'loan'
    if (args.get('loan_uploaded') or '').strip() == '1':
        return 'loan'
    return 'contribution'


def _gsis_loan_record_to_loan_options(rec: GsisLoanRecord) -> list[dict]:
    opts = []
    for field, label in GSIS_LOAN_TYPE_FIELDS:
        amt = _money_decimal(getattr(rec, field, 0))
        if amt > 0:
            opts.append({'value': field, 'label': label, 'amount': str(amt), 'amount_display': _money_str(amt)})
    return opts


def _gsis_loan_totals(rec: GsisLoanRecord) -> tuple[Decimal, str, list[dict]]:
    """Sum loan amounts and build comma-separated labels plus detail rows."""
    labels: list[str] = []
    details: list[dict] = []
    total = Decimal('0.00')
    for field, label in GSIS_LOAN_TYPE_FIELDS:
        amt = _money_decimal(getattr(rec, field, 0))
        if amt > 0:
            labels.append(label)
            total += amt
            details.append({
                'value': field,
                'label': label,
                'amount': str(amt),
                'amount_display': _money_str(amt),
            })
    return total.quantize(Decimal('0.01')), ', '.join(labels), details


def _gsis_loan_types_storage_key(label: str) -> str:
    """Persist aggregated loan label when it fits loan_type column."""
    label = (label or '').strip()
    if not label:
        return 'combined'
    return label if len(label) <= 32 else 'combined'


def _gsis_loan_load_rows(year: int, month: int, department_id: int | None) -> list[dict]:
    q = (GsisLoanRecord.query
         .join(Employee, Employee.id == GsisLoanRecord.employee_id)
         .options(db.joinedload(GsisLoanRecord.employee).joinedload(Employee.department))
         .filter(GsisLoanRecord.year == year, GsisLoanRecord.month == month))
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    records = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()

    all_deds = GsisLoanDeduction.query.filter_by(year=year, month=month).all()
    ded_by_emp: dict[int, list[GsisLoanDeduction]] = {}
    for d in all_deds:
        ded_by_emp.setdefault(d.employee_id, []).append(d)

    rows = []
    for rec in records:
        emp = rec.employee
        if not emp or _is_jo_cos_employee(emp):
            continue
        loan_opts = _gsis_loan_record_to_loan_options(rec)
        if not loan_opts:
            continue

        upload_total, loan_types_label, loan_details = _gsis_loan_totals(rec)
        emp_deds = ded_by_emp.get(emp.id, [])
        if emp_deds:
            month_amt = sum(_money_decimal(d.month_amount) for d in emp_deds).quantize(Decimal('0.01'))
            ded = emp_deds[0]
            q1_amt = _money_decimal(ded.q1_amount)
            q2_amt = _money_decimal(ded.q2_amount)
            q1_on = bool(ded.q1_enabled)
            q2_on = bool(ded.q2_enabled)
        else:
            month_amt = upload_total
            q1_amt, q2_amt = _gsis_loan_quincena_split(month_amt)
            q1_on = q2_on = True

        loan_type = _gsis_loan_types_storage_key(loan_types_label)
        rows.append({
            'employee_pk': emp.id,
            'employee_id': emp.employee_id,
            'employee_name': emp.directory_name,
            'department_name': (emp.department.name if emp.department else ''),
            'loan_type': loan_type,
            'loan_types_label': loan_types_label,
            'loan_details': loan_details,
            'loan_options': loan_opts,
            'month_amount': _money_str(month_amt),
            'month_amount_raw': str(month_amt),
            'q1_amount': _money_str(q1_amt),
            'q1_amount_raw': str(q1_amt),
            'q2_amount': _money_str(q2_amt),
            'q2_amount_raw': str(q2_amt),
            'q1_enabled': q1_on,
            'q2_enabled': q2_on,
            'search_text': f"{emp.employee_id} {emp.last_name} {emp.first_name} {loan_types_label}".lower(),
        })
    return rows


LBP_BILLING_LABEL = 'LBP'


def _normalize_employee_no(val) -> str:
    """Normalize employee number from spreadsheet cells for matching."""
    if val is None:
        return ''
    try:
        import pandas as pd
        if pd.isna(val):
            return ''
    except Exception:
        pass
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val == int(val):
            return str(int(val))
        return str(int(val)) if isinstance(val, float) else str(val)
    s = str(val).strip()
    if re.fullmatch(r'\d+\.0+', s):
        s = s.split('.')[0]
    return s


def _payroll_billing_employee_by_no() -> dict[str, Employee]:
    """Map normalized employee number to active plantilla employees."""
    out: dict[str, Employee] = {}
    rows = (Employee.query
            .options(db.joinedload(Employee.department))
            .filter_by(status='active')
            .all())
    for emp in rows:
        if _is_jo_cos_employee(emp):
            continue
        key = _normalize_employee_no(emp.employee_id)
        if key:
            out[key] = emp
    return out


def _payroll_billing_col_index(col_index: dict[str, int], *aliases: str) -> int | None:
    norm_map = {
        re.sub(r'[^A-Z0-9]', '', k.upper()): idx
        for k, idx in col_index.items()
    }
    for alias in aliases:
        key = re.sub(r'[^A-Z0-9]', '', alias.upper())
        if key in norm_map:
            return norm_map[key]
    return None


def _lbp_billing_employee_by_no() -> dict[str, Employee]:
    return _payroll_billing_employee_by_no()


def _lbp_billing_col_index(col_index: dict[str, int], *aliases: str) -> int | None:
    return _payroll_billing_col_index(col_index, *aliases)


def _lbp_billing_sheet_name(sheet_names: list[str]) -> str | None:
    for name in sheet_names:
        if name.strip().lower() == 'lbp billing':
            return name
    return None


def _parse_lbp_billing_xls(file_bytes: bytes) -> dict:
    import pandas as pd

    xl = pd.ExcelFile(io.BytesIO(file_bytes))
    sheet_name = _lbp_billing_sheet_name(list(xl.sheet_names))
    if not sheet_name:
        raise ValueError('Could not find sheet "lbp billing" in the uploaded file.')

    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name, header=None)
    header_row = None
    col_index: dict[str, int] = {}
    for i in range(min(25, len(df))):
        row_vals = [str(x).strip() for x in df.iloc[i].tolist()]
        upper_vals = [v.upper() for v in row_vals]
        has_emp = any(v in ('EMPLOYEE NO.', 'EMPLOYEE NO') for v in upper_vals)
        has_percov = any(v == 'PERCOV' for v in upper_vals)
        if has_emp and has_percov:
            header_row = i
            for j, val in enumerate(row_vals):
                key = str(val).strip().upper()
                if key:
                    col_index[key] = j
            break
    if header_row is None:
        raise ValueError('Could not find header row (Employee No., PERCOV) in the LBP billing sheet.')

    emp_no_idx = _lbp_billing_col_index(col_index, 'Employee No.', 'Employee No')
    percov_idx = _lbp_billing_col_index(col_index, 'PERCOV')
    q1_idx = _lbp_billing_col_index(col_index, 'Q1')
    q2_idx = _lbp_billing_col_index(col_index, 'Q2')
    total_idx = _lbp_billing_col_index(col_index, 'Total Payable')
    if emp_no_idx is None or percov_idx is None:
        raise ValueError('Employee No. and PERCOV columns are required in the LBP billing sheet.')
    if q1_idx is None or q2_idx is None or total_idx is None:
        raise ValueError('Q1, Q2, and Total Payable columns are required in the LBP billing sheet.')

    year, month = date.today().year, date.today().month
    emp_by_no = _lbp_billing_employee_by_no()
    parsed_rows = []
    unmatched = 0
    period_set = False

    for i in range(header_row + 1, len(df)):
        emp_no_raw = df.iloc[i, emp_no_idx]
        if pd.isna(emp_no_raw) or str(emp_no_raw).strip() == '':
            continue
        emp_no = _normalize_employee_no(emp_no_raw)
        percov_raw = df.iloc[i, percov_idx]
        percov = re.sub(r'\D', '', str(percov_raw or '').strip())
        if not period_set:
            py, pm = _parse_hdmf_percov(percov_raw)
            if py and pm:
                year, month = py, pm
                period_set = True

        def _cell_amount(idx: int) -> Decimal:
            cell = df.iloc[i, idx]
            if pd.isna(cell):
                return Decimal('0.00')
            try:
                return _money_decimal(cell)
            except Exception:
                return Decimal('0.00')

        q1_amt = _cell_amount(q1_idx)
        q2_amt = _cell_amount(q2_idx)
        total_amt = _cell_amount(total_idx)
        emp = emp_by_no.get(emp_no)
        row = {
            'employee_no': emp_no,
            'percov': percov or None,
            'employee_pk': emp.id if emp else None,
            'employee_id': emp.employee_id if emp else '',
            'employee_name': emp.directory_name if emp else '',
            'department_name': (emp.department.name if emp and emp.department else ''),
            'matched': emp is not None,
            'q1_amount': str(q1_amt),
            'q2_amount': str(q2_amt),
            'total_payable': str(total_amt),
            'q1_display': _money_str_or_blank(q1_amt),
            'q2_display': _money_str_or_blank(q2_amt),
            'total_display': _money_str_or_blank(total_amt),
        }
        if not emp:
            unmatched += 1
        parsed_rows.append(row)

    if not parsed_rows:
        raise ValueError('No data rows found in the LBP billing sheet.')

    parsed_rows.sort(key=lambda r: (r.get('employee_name') or 'zzz', r.get('employee_no') or ''))
    return {
        'year': year,
        'month': month,
        'rows': parsed_rows,
        'matched_count': sum(1 for r in parsed_rows if r.get('matched')),
        'unmatched_count': unmatched,
        'total_count': len(parsed_rows),
    }


def _lbp_billing_details(rec: LbpBillingRecord) -> list[dict]:
    return [
        {
            'label': 'Q1',
            'amount': str(_money_decimal(rec.q1_amount)),
            'amount_display': _money_str(rec.q1_amount),
        },
        {
            'label': 'Q2',
            'amount': str(_money_decimal(rec.q2_amount)),
            'amount_display': _money_str(rec.q2_amount),
        },
        {
            'label': 'Total Payable',
            'amount': str(_money_decimal(rec.total_payable)),
            'amount_display': _money_str(rec.total_payable),
        },
    ]


def _lbp_billing_load_rows(year: int, month: int, department_id: int | None) -> list[dict]:
    q = (LbpBillingRecord.query
         .join(Employee, Employee.id == LbpBillingRecord.employee_id)
         .options(db.joinedload(LbpBillingRecord.employee).joinedload(Employee.department))
         .filter(LbpBillingRecord.year == year, LbpBillingRecord.month == month))
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    records = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()

    all_deds = LbpBillingDeduction.query.filter_by(year=year, month=month).all()
    ded_by_emp = {d.employee_id: d for d in all_deds}

    rows = []
    for rec in records:
        emp = rec.employee
        if not emp or _is_jo_cos_employee(emp):
            continue
        total_amt = _money_decimal(rec.total_payable)
        if total_amt <= 0:
            continue

        ded = ded_by_emp.get(emp.id)
        if ded:
            month_amt = _money_decimal(ded.month_amount)
            q1_amt = _money_decimal(ded.q1_amount)
            q2_amt = _money_decimal(ded.q2_amount)
            q1_on = bool(ded.q1_enabled)
            q2_on = bool(ded.q2_enabled)
        else:
            month_amt = total_amt
            q1_amt = _money_decimal(rec.q1_amount)
            q2_amt = _money_decimal(rec.q2_amount)
            q1_on = q2_on = True

        billing_details = _lbp_billing_details(rec)
        rows.append({
            'employee_pk': emp.id,
            'employee_id': emp.employee_id,
            'employee_name': emp.directory_name,
            'department_name': (emp.department.name if emp.department else ''),
            'billing_label': LBP_BILLING_LABEL,
            'billing_details': billing_details,
            'percov': rec.percov or '',
            'month_amount': _money_str(month_amt),
            'month_amount_raw': str(month_amt),
            'q1_amount': _money_str(q1_amt),
            'q1_amount_raw': str(q1_amt),
            'q2_amount': _money_str(q2_amt),
            'q2_amount_raw': str(q2_amt),
            'q1_enabled': q1_on,
            'q2_enabled': q2_on,
            'search_text': f"{emp.employee_id} {emp.last_name} {emp.first_name} {LBP_BILLING_LABEL}".lower(),
        })
    return rows


JOFEMCO_BILLING_LABEL = 'JOFEMCO'

JOFEMCO_DETAIL_FIELDS = (
    ('loan_q1', 'LOAN Q1'),
    ('loan_q2', 'LOAN Q2'),
    ('canteen_q1', 'CANTEEN Q1'),
    ('canteen_q2', 'CANTEEN Q2'),
)


def _jofemco_billing_types_label(rec: JofemcoBillingRecord) -> str:
    labels: list[str] = []
    if _money_decimal(rec.loan_q1) + _money_decimal(rec.loan_q2) > 0:
        labels.append('LOAN')
    if _money_decimal(rec.canteen_q1) + _money_decimal(rec.canteen_q2) > 0:
        labels.append('CANTEEN')
    return ', '.join(labels) if labels else JOFEMCO_BILLING_LABEL


def _jofemco_quincena_totals(rec: JofemcoBillingRecord) -> tuple[Decimal, Decimal, Decimal]:
    loan_q1 = _money_decimal(rec.loan_q1)
    loan_q2 = _money_decimal(rec.loan_q2)
    canteen_q1 = _money_decimal(rec.canteen_q1)
    canteen_q2 = _money_decimal(rec.canteen_q2)
    q1 = (loan_q1 + canteen_q1).quantize(Decimal('0.01'))
    q2 = (loan_q2 + canteen_q2).quantize(Decimal('0.01'))
    total = (q1 + q2).quantize(Decimal('0.01'))
    return q1, q2, total


def _jofemco_find_header(df) -> tuple[int, dict[str, int]] | None:
    for i in range(min(30, len(df))):
        row_vals = [str(x).strip() for x in df.iloc[i].tolist()]
        upper_vals = [v.upper() for v in row_vals]
        has_emp = any(v in ('EMPLOYEE NO.', 'EMPLOYEE NO', 'EMPLOYEE ID') for v in upper_vals)
        has_percov = any(v == 'PERCOV' for v in upper_vals)
        has_loan_q1 = any(v == 'LOAN Q1' for v in upper_vals)
        if has_emp and has_percov and has_loan_q1:
            col_index: dict[str, int] = {}
            for j, val in enumerate(row_vals):
                key = str(val).strip().upper()
                if key:
                    col_index[key] = j
            return i, col_index
    return None


def _parse_jofemco_billing_xls(file_bytes: bytes) -> dict:
    import pandas as pd

    xl = pd.ExcelFile(io.BytesIO(file_bytes))
    sheet_name = None
    df = None
    header_row = None
    col_index: dict[str, int] = {}
    for candidate in xl.sheet_names:
        candidate_df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=candidate, header=None)
        found = _jofemco_find_header(candidate_df)
        if found:
            sheet_name = candidate
            df = candidate_df
            header_row, col_index = found
            break
    if sheet_name is None or df is None or header_row is None:
        raise ValueError(
            'Could not find header row (Employee No./ID, PERCOV, LOAN Q1) in the JOFEMCO file.'
        )

    emp_no_idx = _payroll_billing_col_index(
        col_index, 'Employee No.', 'Employee No', 'Employee ID',
    )
    percov_idx = _payroll_billing_col_index(col_index, 'PERCOV')
    loan_q1_idx = _payroll_billing_col_index(col_index, 'LOAN Q1')
    loan_q2_idx = _payroll_billing_col_index(col_index, 'LOAN Q2')
    canteen_q1_idx = _payroll_billing_col_index(col_index, 'CANTEEN Q1')
    canteen_q2_idx = _payroll_billing_col_index(col_index, 'CANTEEN Q2')
    if emp_no_idx is None or percov_idx is None:
        raise ValueError('Employee No./ID and PERCOV columns are required in the JOFEMCO file.')
    if None in (loan_q1_idx, loan_q2_idx, canteen_q1_idx, canteen_q2_idx):
        raise ValueError('LOAN Q1, LOAN Q2, CANTEEN Q1, and CANTEEN Q2 columns are required.')

    year, month = date.today().year, date.today().month
    emp_by_no = _payroll_billing_employee_by_no()
    parsed_rows = []
    unmatched = 0
    period_set = False

    for i in range(header_row + 1, len(df)):
        emp_no_raw = df.iloc[i, emp_no_idx]
        if pd.isna(emp_no_raw) or str(emp_no_raw).strip() == '':
            continue
        emp_no = _normalize_employee_no(emp_no_raw)
        percov_raw = df.iloc[i, percov_idx]
        percov = re.sub(r'\D', '', str(percov_raw or '').strip())
        if not period_set:
            py, pm = _parse_hdmf_percov(percov_raw)
            if py and pm:
                year, month = py, pm
                period_set = True

        def _cell_amount(idx: int) -> Decimal:
            cell = df.iloc[i, idx]
            if pd.isna(cell):
                return Decimal('0.00')
            try:
                return _money_decimal(cell)
            except Exception:
                return Decimal('0.00')

        loan_q1 = _cell_amount(loan_q1_idx)
        loan_q2 = _cell_amount(loan_q2_idx)
        canteen_q1 = _cell_amount(canteen_q1_idx)
        canteen_q2 = _cell_amount(canteen_q2_idx)
        q1_amt = (loan_q1 + canteen_q1).quantize(Decimal('0.01'))
        q2_amt = (loan_q2 + canteen_q2).quantize(Decimal('0.01'))
        total_amt = (q1_amt + q2_amt).quantize(Decimal('0.01'))
        if total_amt <= 0:
            continue

        emp = emp_by_no.get(emp_no)
        row = {
            'employee_no': emp_no,
            'percov': percov or None,
            'employee_pk': emp.id if emp else None,
            'employee_id': emp.employee_id if emp else '',
            'employee_name': emp.directory_name if emp else '',
            'department_name': (emp.department.name if emp and emp.department else ''),
            'matched': emp is not None,
            'loan_q1': str(loan_q1),
            'loan_q2': str(loan_q2),
            'canteen_q1': str(canteen_q1),
            'canteen_q2': str(canteen_q2),
            'q1_amount': str(q1_amt),
            'q2_amount': str(q2_amt),
            'total_payable': str(total_amt),
            'loan_q1_display': _money_str_or_blank(loan_q1),
            'loan_q2_display': _money_str_or_blank(loan_q2),
            'canteen_q1_display': _money_str_or_blank(canteen_q1),
            'canteen_q2_display': _money_str_or_blank(canteen_q2),
            'total_display': _money_str_or_blank(total_amt),
        }
        if not emp:
            unmatched += 1
        parsed_rows.append(row)

    if not parsed_rows:
        raise ValueError('No data rows found in the JOFEMCO file.')

    parsed_rows.sort(key=lambda r: (r.get('employee_name') or 'zzz', r.get('employee_no') or ''))
    return {
        'year': year,
        'month': month,
        'sheet_name': sheet_name,
        'rows': parsed_rows,
        'matched_count': sum(1 for r in parsed_rows if r.get('matched')),
        'unmatched_count': unmatched,
        'total_count': len(parsed_rows),
    }


def _jofemco_billing_details(rec: JofemcoBillingRecord) -> list[dict]:
    details = []
    for field, label in JOFEMCO_DETAIL_FIELDS:
        amt = _money_decimal(getattr(rec, field, 0))
        if amt > 0:
            details.append({
                'label': label,
                'amount': str(amt),
                'amount_display': _money_str(amt),
            })
    total = _money_decimal(rec.total_payable)
    details.append({
        'label': 'Total Payable',
        'amount': str(total),
        'amount_display': _money_str(total),
    })
    return details


def _jofemco_billing_load_rows(year: int, month: int, department_id: int | None) -> list[dict]:
    q = (JofemcoBillingRecord.query
         .join(Employee, Employee.id == JofemcoBillingRecord.employee_id)
         .options(db.joinedload(JofemcoBillingRecord.employee).joinedload(Employee.department))
         .filter(JofemcoBillingRecord.year == year, JofemcoBillingRecord.month == month))
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    records = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()

    all_deds = JofemcoBillingDeduction.query.filter_by(year=year, month=month).all()
    ded_by_emp = {d.employee_id: d for d in all_deds}

    rows = []
    for rec in records:
        emp = rec.employee
        if not emp or _is_jo_cos_employee(emp):
            continue
        upload_q1, upload_q2, total_amt = _jofemco_quincena_totals(rec)
        if total_amt <= 0:
            continue

        ded = ded_by_emp.get(emp.id)
        if ded:
            month_amt = _money_decimal(ded.month_amount)
            q1_amt = _money_decimal(ded.q1_amount)
            q2_amt = _money_decimal(ded.q2_amount)
            q1_on = bool(ded.q1_enabled)
            q2_on = bool(ded.q2_enabled)
        else:
            month_amt = total_amt
            q1_amt = upload_q1
            q2_amt = upload_q2
            q1_on = q2_on = True

        billing_label = _jofemco_billing_types_label(rec)
        rows.append({
            'employee_pk': emp.id,
            'employee_id': emp.employee_id,
            'employee_name': emp.directory_name,
            'department_name': (emp.department.name if emp.department else ''),
            'billing_label': billing_label,
            'billing_details': _jofemco_billing_details(rec),
            'percov': rec.percov or '',
            'month_amount': _money_str(month_amt),
            'month_amount_raw': str(month_amt),
            'q1_amount': _money_str(q1_amt),
            'q1_amount_raw': str(q1_amt),
            'q2_amount': _money_str(q2_amt),
            'q2_amount_raw': str(q2_amt),
            'q1_enabled': q1_on,
            'q2_enabled': q2_on,
            'search_text': f"{emp.employee_id} {emp.last_name} {emp.first_name} {billing_label}".lower(),
        })
    return rows


WITHHOLDING_TAX_LABEL = 'Withholding Tax'


def _withholding_tax_find_header(df) -> tuple[int, dict[str, int]] | None:
    for i in range(min(35, len(df))):
        row_vals = [str(x).strip() for x in df.iloc[i].tolist()]
        upper_vals = [v.upper() for v in row_vals]
        has_emp = any(v in ('EMPLOYEE NO.', 'EMPLOYEE NO', 'EMPLOYEE ID') for v in upper_vals)
        has_percov = any(v == 'PERCOV' for v in upper_vals)
        has_q1 = any(v in ('Q1 ONLY', 'Q1ONLY') for v in upper_vals)
        if has_emp and has_percov and has_q1:
            col_index: dict[str, int] = {}
            for j, val in enumerate(row_vals):
                key = str(val).strip().upper()
                if key:
                    col_index[key] = j
            return i, col_index
    return None


def _parse_withholding_tax_xlsx(file_bytes: bytes) -> dict:
    import pandas as pd

    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name='TAX_SCHEDULE', header=None)
    found = _withholding_tax_find_header(df)
    if not found:
        raise ValueError('Could not find header row (Employee No., PERCOV, Q1 ONLY) in TAX_SCHEDULE sheet.')
    header_row, col_index = found

    emp_no_idx = _payroll_billing_col_index(col_index, 'Employee No.', 'Employee No', 'Employee ID')
    percov_idx = _payroll_billing_col_index(col_index, 'PERCOV')
    q1_idx = _payroll_billing_col_index(col_index, 'Q1 ONLY', 'Q1ONLY')
    if emp_no_idx is None or percov_idx is None or q1_idx is None:
        raise ValueError('Employee No., PERCOV, and Q1 ONLY columns are required in TAX_SCHEDULE.')

    year, month = date.today().year, date.today().month
    emp_by_no = _payroll_billing_employee_by_no()
    parsed_rows = []
    unmatched = 0
    period_set = False

    for i in range(header_row + 1, len(df)):
        emp_no_raw = df.iloc[i, emp_no_idx]
        if pd.isna(emp_no_raw) or str(emp_no_raw).strip() == '':
            continue
        emp_no = _normalize_employee_no(emp_no_raw)
        percov_raw = df.iloc[i, percov_idx]
        percov = re.sub(r'\D', '', str(percov_raw or '').strip())
        if not period_set:
            py, pm = _parse_hdmf_percov(percov_raw)
            if py and pm:
                year, month = py, pm
                period_set = True

        cell = df.iloc[i, q1_idx]
        if pd.isna(cell):
            q1_amt = Decimal('0.00')
        else:
            try:
                q1_amt = _money_decimal(cell)
            except Exception:
                q1_amt = Decimal('0.00')
        if q1_amt <= 0:
            continue

        emp = emp_by_no.get(emp_no)
        row = {
            'employee_no': emp_no,
            'percov': percov or None,
            'employee_pk': emp.id if emp else None,
            'employee_id': emp.employee_id if emp else '',
            'employee_name': emp.directory_name if emp else '',
            'department_name': (emp.department.name if emp and emp.department else ''),
            'matched': emp is not None,
            'q1_amount': str(q1_amt),
            'q1_display': _money_str_or_blank(q1_amt),
        }
        if not emp:
            unmatched += 1
        parsed_rows.append(row)

    if not parsed_rows:
        raise ValueError('No data rows found in the TAX_SCHEDULE sheet.')

    parsed_rows.sort(key=lambda r: (r.get('employee_name') or 'zzz', r.get('employee_no') or ''))
    return {
        'year': year,
        'month': month,
        'rows': parsed_rows,
        'matched_count': sum(1 for r in parsed_rows if r.get('matched')),
        'unmatched_count': unmatched,
        'total_count': len(parsed_rows),
    }


def _withholding_tax_load_rows(year: int, month: int, department_id: int | None) -> list[dict]:
    q = (WithholdingTaxRecord.query
         .join(Employee, Employee.id == WithholdingTaxRecord.employee_id)
         .options(db.joinedload(WithholdingTaxRecord.employee).joinedload(Employee.department))
         .filter(WithholdingTaxRecord.year == year, WithholdingTaxRecord.month == month))
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    records = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()

    all_deds = WithholdingTaxDeduction.query.filter_by(year=year, month=month).all()
    ded_by_emp = {d.employee_id: d for d in all_deds}

    rows = []
    for rec in records:
        emp = rec.employee
        if not emp or _is_jo_cos_employee(emp):
            continue
        upload_q1 = _money_decimal(rec.q1_amount)
        if upload_q1 <= 0:
            continue

        ded = ded_by_emp.get(emp.id)
        if ded:
            q1_amt = _money_decimal(ded.q1_amount)
            month_amt = _money_decimal(ded.month_amount)
        else:
            q1_amt = upload_q1
            month_amt = upload_q1

        rows.append({
            'employee_pk': emp.id,
            'employee_id': emp.employee_id,
            'employee_name': emp.directory_name,
            'department_name': (emp.department.name if emp.department else ''),
            'label': WITHHOLDING_TAX_LABEL,
            'percov': rec.percov or '',
            'month_amount': _money_str(month_amt),
            'month_amount_raw': str(month_amt),
            'q1_amount': _money_str(q1_amt),
            'q1_amount_raw': str(q1_amt),
            'search_text': f"{emp.employee_id} {emp.last_name} {emp.first_name} {WITHHOLDING_TAX_LABEL}".lower(),
        })
    return rows


def _gsis_contribution_row_dict(emp: Employee) -> dict:
    """Build display + raw payload for one plantilla employee GSIS row."""
    basic = _current_salary_amount(emp) or 0.0
    amounts = _gsis_contribution_amounts(basic)
    return {
        'employee_pk': emp.id,
        'employee_id': emp.employee_id,
        'employee_name': emp.directory_name,
        'department_name': (emp.department.name if emp.department else ''),
        'basic_salary': _money_str(amounts['basic_salary']),
        'ps': _money_str(amounts['ps_amount']),
        'gs': _money_str(amounts['gs_amount']),
        'month_amount': _money_str(amounts['month_amount']),
        'ps_raw': str(amounts['ps_amount']),
        'gs_raw': str(amounts['gs_amount']),
        'month_amount_raw': str(amounts['month_amount']),
    }


HDMF_DEDUCTIBLE_QUINCENA = '2'
HDMF_JO_COS_DEDUCTIBLE_QUINCENA = '1'

HDMF_LOAN_TYPE_FIELDS = (
    ('mpl', 'MPL'),
    ('salary', 'SALARY'),
    ('housing', 'HOUSING'),
    ('safe', 'SAFE'),
)

HDMF_SCOPE_LABELS = {
    'jo_cos': 'JO/COS',
    'plantilla': 'Plantilla',
}

HDMF_CLASSIFICATION = {
    ('jo_cos', 'contribution'): 'hdmf_jo_cos_contribution',
    ('jo_cos', 'loan'): 'hdmf_jo_cos_loan',
    ('plantilla', 'contribution'): 'hdmf_plantilla_contribution',
    ('plantilla', 'loan'): 'hdmf_plantilla_loan',
}


def _hdmf_url_scope(scope: str) -> str:
    return 'jo-cos' if scope == 'jo_cos' else 'plantilla'


def _hdmf_employment_scope(url_scope: str) -> str | None:
    if url_scope == 'jo-cos':
        return 'jo_cos'
    if url_scope == 'plantilla':
        return 'plantilla'
    return None


def _hdmf_employee_matches_scope(emp: Employee, employment_scope: str) -> bool:
    is_jo = _is_jo_cos_employee(emp)
    return is_jo if employment_scope == 'jo_cos' else not is_jo


def _normalize_hdmf_mid(val) -> str:
    return re.sub(r'\D', '', str(val or '').strip())


def _parse_hdmf_percov(val) -> tuple[int | None, int | None]:
    s = re.sub(r'\D', '', str(val or '').strip())
    if len(s) >= 6:
        try:
            year = int(s[:4])
            month = int(s[4:6])
            if 1 <= month <= 12:
                return year, month
        except ValueError:
            pass
    return None, None


def _read_hdmf_excel_sheet(file_bytes: bytes, sheet_name: str):
    import pandas as pd

    bio = io.BytesIO(file_bytes)
    try:
        return pd.read_excel(bio, sheet_name=sheet_name, header=None, engine='openpyxl')
    except Exception:
        bio.seek(0)
        return pd.read_excel(bio, sheet_name=sheet_name, header=None, engine='xlrd')


def _hdmf_employee_by_mid(employment_scope: str) -> dict[str, Employee]:
    out: dict[str, Employee] = {}
    rows = (Employee.query
            .options(db.joinedload(Employee.pds), db.joinedload(Employee.department))
            .filter_by(status='active')
            .all())
    for emp in rows:
        pds = emp.pds
        if not pds or not (pds.pagibig_id or '').strip():
            continue
        key = _normalize_hdmf_mid(pds.pagibig_id)
        if key:
            out[key] = emp
    return out


def _parse_hdmf_contribution_xlsx(file_bytes: bytes, employment_scope: str) -> dict:
    import pandas as pd

    df = _read_hdmf_excel_sheet(file_bytes, 'Contribution')
    header_sub_row = None
    for i in range(min(20, len(df))):
        if str(df.iloc[i, 0] or '').strip().upper() == 'MID NO.':
            header_sub_row = i
            break
    if header_sub_row is None:
        raise ValueError('Could not find MID NO. header row in the Contribution sheet.')

    # Determine covered period from PERCOV column (e.g. 202605 => May 2026).
    # JO/COS: use the first valid PERCOV encountered.
    # Plantilla: use the last valid PERCOV encountered.
    year, month = date.today().year, date.today().month
    period_year = None
    period_month = None
    emp_by_mid = _hdmf_employee_by_mid(employment_scope)
    parsed_rows = []
    unmatched = 0
    data_start = header_sub_row + 1
    for i in range(data_start, len(df)):
        mid_raw = df.iloc[i, 0]
        if pd.isna(mid_raw) or str(mid_raw).strip() == '':
            continue
        mid = _normalize_hdmf_mid(mid_raw)
        if not mid:
            continue
        row_year, row_month = _parse_hdmf_percov(df.iloc[i, 7] if df.shape[1] > 7 else None)
        if row_year and row_month:
            if employment_scope == 'plantilla':
                period_year, period_month = row_year, row_month
            elif period_year is None or period_month is None:
                period_year, period_month = row_year, row_month
        mp2_account = str(df.iloc[i, 1] or '').strip() if df.shape[1] > 1 and not pd.isna(df.iloc[i, 1]) else ''
        membership_program = str(df.iloc[i, 2] or '').strip() if df.shape[1] > 2 else ''
        if not membership_program:
            membership_program = 'Unknown'
        er_share = _money_decimal(df.iloc[i, 9] if df.shape[1] > 9 else 0)
        ee_share = _money_decimal(df.iloc[i, 10] if df.shape[1] > 10 else 0)
        emp = emp_by_mid.get(mid)
        row = {
            'mid_no': mid,
            'mp2_account_no': mp2_account,
            'membership_program': membership_program,
            'percov': str(df.iloc[i, 7] or '').strip() if df.shape[1] > 7 else '',
            'er_share': str(er_share),
            'ee_share': str(ee_share),
            'er_share_display': _money_str_or_blank(er_share),
            'ee_share_display': _money_str_or_blank(ee_share),
            'employee_pk': emp.id if emp else None,
            'employee_id': emp.employee_id if emp else '',
            'employee_name': emp.directory_name if emp else '',
            'department_name': (emp.department.name if emp and emp.department else ''),
            'matched': emp is not None,
        }
        if not emp:
            unmatched += 1
        parsed_rows.append(row)

    if period_year and period_month:
        year, month = period_year, period_month

    parsed_rows.sort(key=lambda r: (r.get('employee_name') or 'zzz', r.get('mid_no') or ''))
    return {
        'year': year,
        'month': month,
        'rows': parsed_rows,
        'matched_count': sum(1 for r in parsed_rows if r.get('matched')),
        'unmatched_count': unmatched,
        'total_count': len(parsed_rows),
    }


def _parse_hdmf_loan_xlsx(file_bytes: bytes, employment_scope: str) -> dict:
    import pandas as pd

    df = _read_hdmf_excel_sheet(file_bytes, 'Loan')
    header_row = None
    for i in range(min(20, len(df))):
        label = str(df.iloc[i, 0] or '').strip().upper()
        if label in ('PAG-IBIG MID NO.', 'PAGIBIG MID NO.'):
            header_row = i
            break
    if header_row is None:
        raise ValueError('Could not find Pag-IBIG MID NO. header row in the Loan sheet.')

    header_vals = [str(x).strip().upper() for x in df.iloc[header_row].tolist()]
    col_index: dict[str, int] = {}
    for j, val in enumerate(header_vals):
        if val:
            col_index[val.replace(' ', '_')] = j

    def _col(*names: str) -> int | None:
        for name in names:
            idx = col_index.get(name.replace(' ', '_').upper())
            if idx is not None:
                return idx
        return None

    mid_idx = _col('PAG-IBIG_MID_NO.', 'PAGIBIG_MID_NO.')
    if mid_idx is None:
        mid_idx = 0
    percov_idx = _col('PERCOV')
    amount_cols = {
        'mpl': _col('MPL'),
        'salary': _col('SALARY'),
        'housing': _col('HOUSING'),
        'safe': _col('SAFE'),
    }

    # Determine covered period from PERCOV column when present (e.g. 202605 => May 2026).
    # If not present, fall back to today's year/month.
    year, month = date.today().year, date.today().month
    period_year = None
    period_month = None
    emp_by_mid = _hdmf_employee_by_mid(employment_scope)
    parsed_rows = []
    unmatched = 0
    data_start = header_row + 1
    while data_start < len(df):
        first = df.iloc[data_start, mid_idx] if mid_idx < df.shape[1] else None
        if not pd.isna(first) and str(first).strip():
            break
        data_start += 1

    for i in range(data_start, len(df)):
        mid_raw = df.iloc[i, mid_idx] if mid_idx < df.shape[1] else None
        if pd.isna(mid_raw) or str(mid_raw).strip() == '':
            continue
        mid = _normalize_hdmf_mid(mid_raw)
        if not mid:
            continue
        if percov_idx is not None and percov_idx < df.shape[1] and (period_year is None or period_month is None):
            row_year, row_month = _parse_hdmf_percov(df.iloc[i, percov_idx])
            if row_year and row_month:
                period_year, period_month = row_year, row_month
        amounts = {}
        for field, idx in amount_cols.items():
            val = Decimal('0.00')
            if idx is not None and idx < df.shape[1]:
                cell = df.iloc[i, idx]
                if not pd.isna(cell):
                    try:
                        val = _money_decimal(cell)
                    except Exception:
                        val = Decimal('0.00')
            amounts[field] = val
        emp = emp_by_mid.get(mid)
        row = {
            'mid_no': mid,
            'employee_pk': emp.id if emp else None,
            'employee_id': emp.employee_id if emp else '',
            'employee_name': emp.directory_name if emp else '',
            'department_name': (emp.department.name if emp and emp.department else ''),
            'matched': emp is not None,
            **{k: str(v) for k, v in amounts.items()},
            **{f'{k}_display': _money_str_or_blank(v) for k, v in amounts.items()},
        }
        if not emp:
            unmatched += 1
        parsed_rows.append(row)

    if period_year and period_month:
        year, month = period_year, period_month

    parsed_rows.sort(key=lambda r: (r.get('employee_name') or 'zzz', r.get('mid_no') or ''))
    return {
        'year': year,
        'month': month,
        'rows': parsed_rows,
        'matched_count': sum(1 for r in parsed_rows if r.get('matched')),
        'unmatched_count': unmatched,
        'total_count': len(parsed_rows),
    }


def _hdmf_active_tab() -> str:
    args = request.args
    if (args.get('contrib_load') or '').strip() == '1':
        return 'contribution'
    contrib_view = (args.get('contrib_view') or '').strip().lower()
    if contrib_view in ('load', 'upload'):
        return 'contribution'
    if (args.get('contrib_uploaded') or '').strip() == '1':
        return 'contribution'
    if (args.get('loan_load') or '').strip() == '1':
        return 'loan'
    loan_view = (args.get('loan_view') or '').strip().lower()
    if loan_view in ('load', 'upload'):
        return 'loan'
    if (args.get('loan_uploaded') or '').strip() == '1':
        return 'loan'
    return 'contribution'


def _hdmf_membership_program_key(program: str, mp2_acct: str | None) -> tuple[str, str, bool]:
    """Normalize HDMF program name and build a stable account-specific key.

    Upload-submit stores M2/MP2 rows as ``PROGRAM (account)`` in membership_program.
    Returns (base_program, program_key, is_mp2).
    """
    base = (program or 'Unknown').strip()
    acct = (mp2_acct or '').strip()
    is_mp2 = base.lower().startswith('m2-') or base.lower().startswith('mp2')
    if is_mp2 and acct and base.endswith(f'({acct})'):
        base = base[: -(len(acct) + 3)].rstrip()
    if is_mp2 and acct:
        key = f"{base} ({acct})"
        return base, key, True
    return base, base, is_mp2


def _hdmf_program_short_label(base_program: str, is_mp2: bool, mp2_acct: str | None) -> str:
    """Short program label for JO/COS display (F1, MP2, MP2 with account)."""
    if is_mp2:
        acct = (mp2_acct or '').strip()
        return f'MP2 ({acct})' if acct else 'MP2'
    base = (base_program or '').strip().lower()
    if base.startswith('f1') or 'pag-ibig 1' in base:
        return 'F1'
    return (base_program or 'Unknown').strip()


def _hdmf_jo_cos_contribution_summary(
    emp_records: list[HdmfContributionRecord],
) -> tuple[Decimal, Decimal, str, list[dict]]:
    """Aggregate JO/COS contribution PS/GS and build program label + detail rows."""
    ps_amt = gs_amt = Decimal('0.00')
    label_order: list[str] = []
    program_details: list[dict] = []
    has_f1 = False

    for rec in emp_records:
        base, _, is_mp2 = _hdmf_membership_program_key(rec.membership_program, rec.mp2_account_no)
        if is_mp2:
            short = _hdmf_program_short_label(base, True, rec.mp2_account_no)
            amt = _money_decimal(rec.ee_share)
            if short not in label_order:
                label_order.append(short)
            program_details.append({
                'label': short,
                'amount': str(amt),
                'amount_display': _money_str(amt),
            })
        else:
            has_f1 = True
            ps_amt += _money_decimal(rec.ee_share)
            gs_amt += _money_decimal(rec.er_share)

    if has_f1:
        label_order.insert(0, 'F1')
        program_details.insert(0, {
            'label': 'F1',
            'amount': str(ps_amt.quantize(Decimal('0.01'))),
            'amount_display': _money_str(ps_amt),
        })

    program_label = ', '.join(label_order) if label_order else 'F1'
    return ps_amt, gs_amt, program_label, program_details


def _hdmf_plantilla_contribution_totals(emp_records: list[HdmfContributionRecord]) -> tuple[Decimal, Decimal, Decimal, str]:
    """Aggregate F1 PS/GS and total MP2 from uploaded contribution records."""
    ps_amt = gs_amt = mp2_amt = Decimal('0.00')
    has_f1 = has_mp2 = False
    for rec in emp_records:
        _, _, is_mp2 = _hdmf_membership_program_key(rec.membership_program, rec.mp2_account_no)
        if is_mp2:
            has_mp2 = True
            mp2_amt += _money_decimal(rec.ee_share)
        else:
            has_f1 = True
            ps_amt += _money_decimal(rec.ee_share)
            gs_amt += _money_decimal(rec.er_share)
    if has_mp2:
        program_label = 'F1 & MP2'
    else:
        program_label = 'F1 only'
    return ps_amt, gs_amt, mp2_amt.quantize(Decimal('0.01')), program_label


def _hdmf_plantilla_contribution_details(emp_records: list[HdmfContributionRecord]) -> list[dict]:
    """Build program breakdown rows for Plantilla contribution expand view."""
    details: list[dict] = []
    f1_ps = f1_gs = Decimal('0.00')
    has_f1 = False

    for rec in emp_records:
        base, _, is_mp2 = _hdmf_membership_program_key(rec.membership_program, rec.mp2_account_no)
        if is_mp2:
            short = _hdmf_program_short_label(base, True, rec.mp2_account_no)
            amt = _money_decimal(rec.ee_share)
            details.append({
                'label': short,
                'amount': str(amt),
                'amount_display': _money_str(amt),
            })
        else:
            has_f1 = True
            f1_ps += _money_decimal(rec.ee_share)
            f1_gs += _money_decimal(rec.er_share)

    if has_f1:
        details.insert(0, {
            'label': 'F1 (GS)',
            'amount': str(f1_gs.quantize(Decimal('0.01'))),
            'amount_display': _money_str(f1_gs),
        })
        details.insert(0, {
            'label': 'F1 (PS)',
            'amount': str(f1_ps.quantize(Decimal('0.01'))),
            'amount_display': _money_str(f1_ps),
        })

    return details


def _hdmf_contribution_program_options(records: list[HdmfContributionRecord]) -> list[dict]:
    opts = []
    for rec in records:
        base_program, value, is_mp2 = _hdmf_membership_program_key(
            rec.membership_program, rec.mp2_account_no,
        )
        label = base_program
        opts.append({
            'value': value,
            'label': label,
            # F1: PS=EE, GS=ER, MP2=0
            # M2/MP2: PS/GS=0, MP2=EE (file amount)
            'ps_amount': '0.00' if is_mp2 else str(_money_decimal(rec.ee_share)),
            'gs_amount': '0.00' if is_mp2 else str(_money_decimal(rec.er_share)),
            'mp2_amount': str(_money_decimal(rec.ee_share)) if is_mp2 else '0.00',
            'has_mp2': bool((rec.mp2_account_no or '').strip()),
        })
    return opts


def _hdmf_contribution_load_rows(
    employment_scope: str, year: int, month: int, department_id: int | None,
) -> list[dict]:
    q = (HdmfContributionRecord.query
         .join(Employee, Employee.id == HdmfContributionRecord.employee_id)
         .options(db.joinedload(HdmfContributionRecord.employee).joinedload(Employee.department))
         .filter(
             HdmfContributionRecord.year == year,
             HdmfContributionRecord.month == month,
             HdmfContributionRecord.employment_scope == employment_scope,
         ))
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    records = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()

    by_emp: dict[int, list[HdmfContributionRecord]] = {}
    for rec in records:
        by_emp.setdefault(rec.employee_id, []).append(rec)

    ded_map = {
        d.employee_id: d
        for d in HdmfContributionDeduction.query.filter_by(
            year=year, month=month, employment_scope=employment_scope,
        ).all()
    }

    rows = []
    for emp_id, emp_records in by_emp.items():
        emp = emp_records[0].employee
        if not emp or not _hdmf_employee_matches_scope(emp, employment_scope):
            continue
        ded = ded_map.get(emp_id)
        if employment_scope == 'plantilla':
            upload_ps, upload_gs, upload_mp2, program_label = _hdmf_plantilla_contribution_totals(emp_records)
            if ded:
                ps_amt = _money_decimal(ded.ps_amount)
                gs_amt = _money_decimal(ded.gs_amount)
                mp2_amt = _money_decimal(ded.mp2_amount)
            else:
                ps_amt = upload_ps
                gs_amt = upload_gs
                mp2_amt = upload_mp2
            program = program_label
            program_details = _hdmf_plantilla_contribution_details(emp_records)
            month_amt = (ps_amt + mp2_amt).quantize(Decimal('0.01'))
            rows.append({
                'employee_pk': emp.id,
                'employee_id': emp.employee_id,
                'employee_name': emp.directory_name,
                'department_name': (emp.department.name if emp.department else ''),
                'membership_program': program,
                'program_label': program_label,
                'program_details': program_details,
                'program_options': [],
                'ps_amount': _money_str(ps_amt),
                'ps_amount_raw': str(ps_amt),
                'gs_amount': _money_str(gs_amt),
                'gs_amount_raw': str(gs_amt),
                'mp2_amount': _money_str(mp2_amt),
                'mp2_amount_raw': str(mp2_amt),
                'month_amount': _money_str(month_amt),
                'month_amount_raw': str(month_amt),
                'search_text': f"{emp.employee_id} {emp.last_name} {emp.first_name} {program_label}".lower(),
            })
            continue

        if employment_scope == 'jo_cos':
            upload_ps, upload_gs, program_label, program_details = _hdmf_jo_cos_contribution_summary(emp_records)
            if ded:
                ps_amt = _money_decimal(ded.ps_amount)
                gs_amt = _money_decimal(ded.gs_amount)
            else:
                ps_amt = upload_ps
                gs_amt = upload_gs
            program = program_label
            month_amt = ps_amt.quantize(Decimal('0.01'))
            rows.append({
                'employee_pk': emp.id,
                'employee_id': emp.employee_id,
                'employee_name': emp.directory_name,
                'department_name': (emp.department.name if emp.department else ''),
                'membership_program': program,
                'program_label': program_label,
                'program_details': program_details,
                'program_options': [],
                'ps_amount': _money_str(ps_amt),
                'ps_amount_raw': str(ps_amt),
                'gs_amount': _money_str(gs_amt),
                'gs_amount_raw': str(gs_amt),
                'mp2_amount': _money_str(Decimal('0.00')),
                'mp2_amount_raw': '0.00',
                'month_amount': _money_str(month_amt),
                'month_amount_raw': str(month_amt),
                'search_text': f"{emp.employee_id} {emp.last_name} {emp.first_name} {program_label}".lower(),
            })
            continue

        program_opts = _hdmf_contribution_program_options(emp_records)
        if not program_opts:
            continue
        if ded:
            program = ded.membership_program
            ps_amt = _money_decimal(ded.ps_amount)
            gs_amt = _money_decimal(ded.gs_amount)
            mp2_amt = _money_decimal(ded.mp2_amount)
        else:
            program = program_opts[0]['value']
            opt = program_opts[0]
            ps_amt = _money_decimal(opt['ps_amount'])
            gs_amt = _money_decimal(opt['gs_amount'])
            mp2_amt = Decimal('0.00')
        gs_report_amt = gs_amt
        if employment_scope == 'jo_cos':
            mp2_amt = Decimal('0.00')
            month_amt = ps_amt.quantize(Decimal('0.01'))
        else:
            month_amt = (ps_amt + gs_amt + mp2_amt).quantize(Decimal('0.01'))
        program_labels = ' '.join(o['label'] for o in program_opts)
        rows.append({
            'employee_pk': emp.id,
            'employee_id': emp.employee_id,
            'employee_name': emp.directory_name,
            'department_name': (emp.department.name if emp.department else ''),
            'membership_program': program,
            'program_label': program_opts[0]['label'] if len(program_opts) == 1 else program,
            'program_options': program_opts,
            'ps_amount': _money_str(ps_amt),
            'ps_amount_raw': str(ps_amt),
            'gs_amount': _money_str(gs_report_amt),
            'gs_amount_raw': str(gs_report_amt),
            'mp2_amount': _money_str(mp2_amt),
            'mp2_amount_raw': str(mp2_amt),
            'month_amount': _money_str(month_amt),
            'month_amount_raw': str(month_amt),
            'search_text': f"{emp.employee_id} {emp.last_name} {emp.first_name} {program_labels}".lower(),
        })
    rows.sort(key=lambda r: r['employee_name'])
    return rows


def _hdmf_loan_record_to_options(rec: HdmfLoanRecord) -> list[dict]:
    opts = []
    for field, label in HDMF_LOAN_TYPE_FIELDS:
        amt = _money_decimal(getattr(rec, field, 0))
        if amt > 0:
            opts.append({'value': field, 'label': label, 'amount': str(amt), 'amount_display': _money_str(amt)})
    return opts


def _hdmf_plantilla_loan_totals(rec: HdmfLoanRecord) -> tuple[Decimal, str]:
    """Sum all loan amounts and build a comma-separated type label."""
    labels: list[str] = []
    total = Decimal('0.00')
    for field, label in HDMF_LOAN_TYPE_FIELDS:
        amt = _money_decimal(getattr(rec, field, 0))
        if amt > 0:
            labels.append(label)
            total += amt
    return total.quantize(Decimal('0.01')), ', '.join(labels)


def _hdmf_loan_load_rows(
    employment_scope: str, year: int, month: int, department_id: int | None,
) -> list[dict]:
    q = (HdmfLoanRecord.query
         .join(Employee, Employee.id == HdmfLoanRecord.employee_id)
         .options(db.joinedload(HdmfLoanRecord.employee).joinedload(Employee.department))
         .filter(
             HdmfLoanRecord.year == year,
             HdmfLoanRecord.month == month,
             HdmfLoanRecord.employment_scope == employment_scope,
         ))
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    records = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()

    all_deds = HdmfLoanDeduction.query.filter_by(
        year=year, month=month, employment_scope=employment_scope,
    ).all()
    ded_map: dict[tuple[int, str], HdmfLoanDeduction] = {}
    plantilla_ded_by_emp: dict[int, list[HdmfLoanDeduction]] = {}
    for d in all_deds:
        ded_map[(d.employee_id, d.loan_type)] = d
        if employment_scope == 'plantilla':
            plantilla_ded_by_emp.setdefault(d.employee_id, []).append(d)

    rows = []
    for rec in records:
        emp = rec.employee
        if not emp or not _hdmf_employee_matches_scope(emp, employment_scope):
            continue
        loan_opts = _hdmf_loan_record_to_options(rec)
        if not loan_opts:
            continue

        if employment_scope == 'plantilla':
            upload_total, loan_types_label = _hdmf_plantilla_loan_totals(rec)
            emp_deds = plantilla_ded_by_emp.get(emp.id, [])
            if emp_deds:
                month_amt = sum(_money_decimal(d.month_amount) for d in emp_deds).quantize(Decimal('0.01'))
                ded = emp_deds[0]
                q1_amt = _money_decimal(getattr(ded, 'q1_amount', 0))
                q2_amt = _money_decimal(getattr(ded, 'q2_amount', 0))
                q1_on = bool(getattr(ded, 'q1_enabled', True))
                q2_on = bool(getattr(ded, 'q2_enabled', True))
            else:
                month_amt = upload_total
                q1_amt, q2_amt = _gsis_loan_quincena_split(month_amt)
                q1_on = q2_on = True
        else:
            upload_total, loan_types_label = _hdmf_plantilla_loan_totals(rec)
            emp_deds = [d for (eid, _lt), d in ded_map.items() if eid == emp.id]
            if emp_deds:
                month_amt = sum(_money_decimal(d.month_amount) for d in emp_deds).quantize(Decimal('0.01'))
                ded = emp_deds[0]
                q1_amt = _money_decimal(getattr(ded, 'q1_amount', month_amt))
                q2_amt = Decimal('0.00')
                q1_on = True
                q2_on = False
            else:
                month_amt = upload_total
                q1_amt = month_amt
                q2_amt = Decimal('0.00')
                q1_on = True
                q2_on = False

        rows.append({
            'employee_pk': emp.id,
            'employee_id': emp.employee_id,
            'employee_name': emp.directory_name,
            'department_name': (emp.department.name if emp.department else ''),
            'loan_type': loan_types_label,
            'loan_types_label': loan_types_label,
            'loan_details': loan_opts,
            'loan_options': [],
            'month_amount': _money_str(month_amt),
            'month_amount_raw': str(month_amt),
            'q1_amount': _money_str(q1_amt),
            'q1_amount_raw': str(q1_amt),
            'q2_amount': _money_str(q2_amt),
            'q2_amount_raw': str(q2_amt),
            'q1_enabled': q1_on,
            'q2_enabled': q2_on,
            'search_text': f"{emp.employee_id} {emp.last_name} {emp.first_name} {loan_types_label}".lower(),
        })
    return rows


def _hdmf_contribution_deduction_for_quincena(
    ded: HdmfContributionDeduction | None,
    quincena: str,
) -> Decimal:
    if not ded or str(quincena) != str(ded.deductible_quincena):
        return Decimal('0.00')
    return _money_decimal(ded.month_amount)


def _hdmf_loan_deduction_for_quincena(
    loan_deds: list[HdmfLoanDeduction],
    quincena: str,
) -> Decimal:
    total = Decimal('0.00')
    for ded in loan_deds:
        if quincena == '1' and ded.q1_enabled:
            total += _money_decimal(ded.q1_amount)
        elif quincena == '2' and ded.q2_enabled:
            total += _money_decimal(ded.q2_amount)
    return total.quantize(Decimal('0.01'))


def _jo_cos_hdmf_payroll_deduction(
    employee_id: int,
    quincena: str,
    contrib_by_emp: dict[int, HdmfContributionDeduction],
    loans_by_emp: dict[int, list[HdmfLoanDeduction]],
) -> Decimal:
    contrib = _hdmf_contribution_deduction_for_quincena(contrib_by_emp.get(employee_id), quincena)
    loans = _hdmf_loan_deduction_for_quincena(loans_by_emp.get(employee_id, []), quincena)
    return (contrib + loans).quantize(Decimal('0.01'))


def _jo_cos_employee_designation(emp: Employee) -> str:
    if emp.jo_cos_designation and (emp.jo_cos_designation.designation or '').strip():
        return emp.jo_cos_designation.designation.strip()
    return (emp.position or '').strip()


def _jo_cos_rate_for_employee(emp: Employee) -> Decimal | None:
    label = _normalize_jo_cos_rate_label(_jo_cos_employee_designation(emp))
    status = (emp.status_of_appointment or '').strip()
    if label:
        for r in JoCosRate.query.filter_by(status_of_appointment=status).all():
            if _normalize_jo_cos_rate_label(r.designation_label) == label:
                return _money_decimal(r.rate_per_day)
        for r in JoCosRate.query.all():
            if _normalize_jo_cos_rate_label(r.designation_label) == label:
                return _money_decimal(r.rate_per_day)
    basic = JoCosRate.query.get(1)
    return _money_decimal(basic.rate_per_day) if basic else None


def _jo_cos_worktime_totals_for_quincena(emp_id: int, year: int, month: int, quincena: str) -> dict:
    snap = DtrQuincenaWorktimeSummary.query.filter_by(
        employee_id=emp_id, year=year, month=month, quincena_half=quincena
    ).first()
    if snap:
        return {
            'net_mins': int(snap.net_rendered_mins or 0),
            'late_mins': int(snap.late_mins or 0),
            'undertime_mins': int(snap.undertime_mins or 0),
            'gross_mins': int(snap.gross_work_mins or 0),
        }
    totals = {'net_mins': 0, 'late_mins': 0, 'undertime_mins': 0, 'gross_mins': 0}
    for wh in WorkHours.query.filter_by(
        employee_id=emp_id, year=year, month=month, quincena_half=quincena
    ).all():
        totals['net_mins'] += int(wh.net_rendered_mins or 0)
        totals['late_mins'] += int(wh.late_mins or 0)
        totals['undertime_mins'] += int(wh.undertime_mins or 0)
        totals['gross_mins'] += int(wh.gross_work_mins or 0)
    return totals


def _jo_cos_ot_mins_for_quincena(emp_id: int, year: int, month: int, quincena: str) -> int:
    total = (
        db.session.query(func.coalesce(func.sum(JoCosOvertime.overtime_mins), 0))
        .filter_by(employee_id=emp_id, year=year, month=month, quincena_half=quincena)
        .scalar()
    )
    return int(total or 0)


def _jo_cos_payroll_parse_filters(args) -> dict:
    return _payroll_quincena_parse_filters(args)


def _payroll_quincena_parse_filters(args) -> dict:
    today = date.today()
    try:
        year = int((args.get('year') or str(today.year)).strip())
    except ValueError:
        year = today.year
    try:
        month = int((args.get('month') or str(today.month)).strip())
    except ValueError:
        month = today.month
    quincena = (args.get('quincena') or ('1' if today.day <= 15 else '2')).strip()
    if quincena not in ('1', '2'):
        quincena = '1'
    if month < 1 or month > 12:
        month = today.month
    if year < 2000 or year > 2100:
        year = today.year
    dept_raw = (args.get('department_id') or '').strip()
    loaded = (args.get('load') or '').strip().lower() in ('1', 'true', 'yes')
    return {
        'year': year,
        'month': month,
        'quincena': quincena,
        'department_id': dept_raw,
        'loaded': loaded,
    }


def _jo_cos_payroll_period_label(filters: dict, dept_name: str | None = None) -> str:
    month_name = date(filters['year'], filters['month'], 1).strftime('%B %Y')
    q_label = '1st' if filters['quincena'] == '1' else '2nd'
    parts = [f'{month_name} — {q_label} quincena']
    if dept_name:
        parts.append(dept_name)
    return ' · '.join(parts)


def _jo_cos_payroll_rows(filters: dict) -> tuple[list[dict], dict]:
    """Build daily-wage payroll rows for active JO/COS employees in the filtered department."""
    try:
        dept_id = int(filters['department_id'])
    except (TypeError, ValueError):
        return [], {'gross_salary': Decimal('0'), 'late': Decimal('0'), 'pagibig': Decimal('0'), 'net': Decimal('0')}

    employees = (
        Employee.query.filter_by(status='active', department_id=dept_id)
        .order_by(Employee.last_name.asc(), Employee.first_name.asc())
        .all()
    )
    employees = [e for e in employees if _is_jo_cos_employee(e)]

    year, month, quincena = filters['year'], filters['month'], filters['quincena']
    start_d, end_d = _quincena_date_range(year, month, quincena)
    emp_ids = [e.id for e in employees]
    contrib_by_emp: dict[int, HdmfContributionDeduction] = {}
    loans_by_emp: dict[int, list[HdmfLoanDeduction]] = {}
    if emp_ids:
        for d in HdmfContributionDeduction.query.filter(
            HdmfContributionDeduction.employee_id.in_(emp_ids),
            HdmfContributionDeduction.year == year,
            HdmfContributionDeduction.month == month,
            HdmfContributionDeduction.employment_scope == 'jo_cos',
        ).all():
            contrib_by_emp[d.employee_id] = d
        for d in HdmfLoanDeduction.query.filter(
            HdmfLoanDeduction.employee_id.in_(emp_ids),
            HdmfLoanDeduction.year == year,
            HdmfLoanDeduction.month == month,
            HdmfLoanDeduction.employment_scope == 'jo_cos',
        ).all():
            loans_by_emp.setdefault(d.employee_id, []).append(d)

    rows = []
    totals = {
        'gross_salary': Decimal('0'),
        'late': Decimal('0'),
        'pagibig': Decimal('0'),
        'net': Decimal('0'),
    }

    for emp in employees:
        wt = _jo_cos_worktime_totals_for_quincena(emp.id, year, month, quincena)
        ot_mins = _jo_cos_ot_mins_for_quincena(emp.id, year, month, quincena)
        rate = _jo_cos_rate_for_employee(emp)
        rate_val = rate if rate is not None else Decimal('0')

        day_mins_val = _jo_cos_payroll_day_mins_for_employee(emp.id, year, month, quincena, start_d, end_d)
        day_mins = Decimal(str(day_mins_val))
        days_worked, days_display = _jo_cos_payroll_days_from_gross_mins(wt['gross_mins'], day_mins_val)
        ot_hours = _mins_to_hours_decimal(ot_mins)
        gross = (days_worked * rate_val).quantize(Decimal('0.01'))
        late_ut_mins = wt['late_mins'] + wt['undertime_mins']
        late_hours = _mins_to_hours_decimal(late_ut_mins)
        late_deduct = ((Decimal(str(late_ut_mins)) / day_mins) * rate_val).quantize(Decimal('0.01'))
        pagibig = _jo_cos_hdmf_payroll_deduction(emp.id, quincena, contrib_by_emp, loans_by_emp)
        net = (gross - late_deduct - pagibig).quantize(Decimal('0.01'))

        name = emp.directory_name_mi

        rows.append({
            'employee_pk': emp.id,
            'employee_no': emp.employee_id or '',
            'employee_name': name,
            'designation': _jo_cos_employee_designation(emp) or '—',
            'days_worked': days_worked,
            'days_worked_display': days_display,
            'overtime_hours': ot_hours,
            'overtime_hours_display': f'{ot_hours:.2f}',
            'rate_per_day': rate_val,
            'rate_per_day_display': _money_str(rate_val) if rate is not None else '—',
            'gross_salary': gross,
            'gross_salary_display': _money_str(gross),
            'late_hours': late_hours,
            'late_hours_display': f'{late_hours:.2f}' if late_hours > 0 else '',
            'late_deduction': late_deduct,
            'late_deduction_display': _money_str(late_deduct) if late_deduct else '',
            'pagibig': pagibig,
            'pagibig_display': _money_str(pagibig) if pagibig else '',
            'net_amount': net,
            'net_amount_display': _money_str(net),
            'search_text': f'{name} {_jo_cos_employee_designation(emp)}'.lower(),
        })
        totals['gross_salary'] += gross
        totals['late'] += late_deduct
        totals['pagibig'] += pagibig
        totals['net'] += net

    for key in totals:
        totals[key] = totals[key].quantize(Decimal('0.01'))
    totals_display = {k: _money_str(v) for k, v in totals.items()}
    return rows, totals_display


def _jo_cos_payroll_pdf_bytes(
    rows: list[dict],
    totals: dict,
    period_label: str,
    department_name: str,
) -> io.BytesIO:
    from reportlab.lib.pagesizes import legal, landscape
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    page_size = landscape(legal)
    c = canvas.Canvas(buf, pagesize=page_size)
    width, height = page_size

    def draw_header(sheet_no: int = 1, sheet_total: int = 1):
        y = height - 36
        c.setFont('Helvetica-Bold', 14)
        c.drawCentredString(width / 2, y, 'DAILY WAGE PAYROLL')
        c.setFont('Helvetica', 8)
        c.drawRightString(width - 36, y + 2, f'Sheet {sheet_no} of {sheet_total} Sheets')
        y -= 16
        c.setFont('Helvetica', 9)
        c.drawString(36, y, f'For labor on {department_name.upper()}, at JAGNA, BOHOL')
        c.drawRightString(width - 36, y, 'ATM')
        y -= 14
        c.drawString(36, y, f'Period: {period_label}')
        return y - 8

    def draw_table_header(y0: float):
        c.setFont('Helvetica-Bold', 6.5)
        y = y0
        xs = [36, 150, 210, 252, 292, 332, 372, 402, 432, 462, 502, 542, 582, 612, 652, 692]
        c.drawString(xs[0], y, 'NAME')
        c.drawString(xs[1], y, 'Designation')
        c.drawString(xs[2], y, 'Days')
        c.drawString(xs[3], y, 'OT Hrs')
        c.drawString(xs[4], y, 'Rate/Day')
        c.drawString(xs[5], y, 'Gross')
        c.drawString(xs[6], y, 'Late')
        c.drawString(xs[7], y, 'Pag-IBIG')
        c.drawString(xs[8], y, 'Net')
        c.drawString(xs[9], y, 'Signature')
        c.drawCentredString(xs[11], y + 8, 'Community Tax')
        c.drawString(xs[10], y, 'No.')
        c.drawString(xs[11], y, 'Date')
        c.drawString(xs[12], y, 'Place')
        c.line(36, y - 3, width - 36, y - 3)
        return y - 12

    y = draw_header()
    y = draw_table_header(y)
    c.setFont('Helvetica', 6.5)
    row_h = 11
    for r in rows:
        if y < 90:
            c.showPage()
            y = draw_header()
            y = draw_table_header(y)
            c.setFont('Helvetica', 6.5)
        xs = [36, 150, 210, 252, 292, 332, 372, 402, 432, 462, 502, 542, 582]
        c.drawString(xs[0], y, str(r.get('employee_name', ''))[:22])
        c.drawString(xs[1], y, str(r.get('designation', ''))[:16])
        c.drawRightString(xs[2] + 22, y, str(r.get('days_worked_display', '')))
        c.drawRightString(xs[3] + 22, y, str(r.get('overtime_hours_display', '')))
        c.drawRightString(xs[4] + 28, y, str(r.get('rate_per_day_display', '')))
        c.drawRightString(xs[5] + 28, y, str(r.get('gross_salary_display', '')))
        c.drawRightString(xs[6] + 22, y, str(r.get('late_deduction_display', '')))
        c.drawRightString(xs[7] + 22, y, str(r.get('pagibig_display', '')))
        c.drawRightString(xs[8] + 28, y, str(r.get('net_amount_display', '')))
        y -= row_h

    y -= 4
    c.line(36, y + 8, width - 36, y + 8)
    c.setFont('Helvetica-Bold', 7)
    c.drawString(36, y - 2, 'TOTAL')
    c.drawRightString(360, y - 2, totals.get('gross_salary', ''))
    c.drawRightString(394, y - 2, totals.get('late', ''))
    c.drawRightString(424, y - 2, totals.get('pagibig', ''))
    c.drawRightString(460, y - 2, totals.get('net', ''))

    sig_y = 56
    c.setFont('Helvetica', 7)
    c.drawString(36, sig_y + 28, 'I hereby certify that the services rendered as stated above were actually performed.')
    c.line(36, sig_y + 12, 220, sig_y + 12)
    c.drawString(36, sig_y, 'Name & Signature of Supervisor')

    c.drawCentredString(width / 2, sig_y + 28, 'Approved for Payment:')
    c.line(width / 2 - 90, sig_y + 12, width / 2 + 90, sig_y + 12)
    c.drawCentredString(width / 2, sig_y, 'Municipal Mayor')

    c.drawRightString(width - 36, sig_y + 28, 'I hereby certify that the persons whose names appear above have been paid.')
    c.line(width - 256, sig_y + 12, width - 36, sig_y + 12)
    c.drawRightString(width - 36, sig_y, 'Name & Signature of Disbursing Officer')

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _jo_cos_payslip_period_label(year: int, month: int, quincena: str) -> str:
    month_name = date(year, month, 1).strftime('%B %Y')
    q_label = '1st' if quincena == '1' else '2nd'
    return f'{month_name} — {q_label} quincena'


def _jo_cos_payslip_view_dict(slip: JoCosPayslip) -> dict:
    period_label = _jo_cos_payslip_period_label(slip.year, slip.month, slip.quincena_half)
    days = _money_decimal(slip.days_worked)
    rate = _money_decimal(slip.rate_per_day)
    gross = _money_decimal(slip.gross_salary)
    late_h = _money_decimal(slip.late_hours)
    late_d = _money_decimal(slip.late_deduction)
    hdmf = _money_decimal(slip.hdmf_deduction)
    net = _money_decimal(slip.net_amount)
    return {
        'id': slip.id,
        'year': slip.year,
        'month': slip.month,
        'quincena': slip.quincena_half,
        'department_id': slip.department_id,
        'employee_name': slip.employee_name,
        'employee_no': slip.employee_no or '',
        'designation': slip.designation or '—',
        'department_name': slip.department_name or '',
        'period_label': period_label,
        'days_worked_display': f'{days:.2f}'.rstrip('0').rstrip('.') if days else '0',
        'rate_per_day_display': _money_str(rate),
        'gross_salary_display': _money_str(gross),
        'late_hours_display': f'{late_h:.2f}' if late_h > 0 else '',
        'late_deduction_display': _money_str(late_d) if late_d else '',
        'hdmf_display': _money_str(hdmf) if hdmf else '',
        'net_amount_display': _money_str(net),
        'generated_at': slip.generated_at,
    }


def _jo_cos_payslip_from_payroll_row(
    row: dict,
    filters: dict,
    department_id: int,
    department_name: str,
    generated_by_user_id: int | None,
) -> JoCosPayslip:
    return JoCosPayslip(
        employee_id=row['employee_pk'],
        department_id=department_id,
        year=filters['year'],
        month=filters['month'],
        quincena_half=filters['quincena'],
        employee_name=row['employee_name'],
        employee_no=row.get('employee_no') or '',
        designation=row.get('designation') or '',
        department_name=department_name,
        days_worked=row['days_worked'],
        rate_per_day=row['rate_per_day'],
        gross_salary=row['gross_salary'],
        late_hours=row['late_hours'],
        late_deduction=row['late_deduction'],
        hdmf_deduction=row['pagibig'],
        net_amount=row['net_amount'],
        generated_by_user_id=generated_by_user_id,
    )


def _jo_cos_payslips_generate(filters: dict, department_name: str, generated_by_user_id: int | None) -> int:
    rows, _ = _jo_cos_payroll_rows(filters)
    if not rows:
        return 0
    dept_id = int(filters['department_id'])
    year, month, quincena = filters['year'], filters['month'], filters['quincena']
    emp_ids = [r['employee_pk'] for r in rows]
    existing = {
        p.employee_id: p
        for p in JoCosPayslip.query.filter(
            JoCosPayslip.employee_id.in_(emp_ids),
            JoCosPayslip.year == year,
            JoCosPayslip.month == month,
            JoCosPayslip.quincena_half == quincena,
        ).all()
    }
    count = 0
    notify_rows: list[tuple[int, int]] = []  # (employee_id, payslip_id)
    for row in rows:
        slip = existing.get(row['employee_pk'])
        if slip:
            slip.department_id = dept_id
            slip.employee_name = row['employee_name']
            slip.employee_no = row.get('employee_no') or ''
            slip.designation = row.get('designation') or ''
            slip.department_name = department_name
            slip.days_worked = row['days_worked']
            slip.rate_per_day = row['rate_per_day']
            slip.gross_salary = row['gross_salary']
            slip.late_hours = row['late_hours']
            slip.late_deduction = row['late_deduction']
            slip.hdmf_deduction = row['pagibig']
            slip.net_amount = row['net_amount']
            slip.generated_at = datetime.utcnow()
            slip.generated_by_user_id = generated_by_user_id
            notify_rows.append((slip.employee_id, slip.id))
        else:
            new_slip = _jo_cos_payslip_from_payroll_row(
                row, filters, dept_id, department_name, generated_by_user_id,
            )
            db.session.add(new_slip)
            db.session.flush()  # assign id so we can link notifications
            notify_rows.append((new_slip.employee_id, new_slip.id))
        count += 1
    # Notify employees (best-effort; notification creation shares same transaction)
    period = _jo_cos_payslip_period_label(filters['year'], filters['month'], filters['quincena'])
    for emp_id, slip_id in notify_rows:
        try:
            emp = Employee.query.get(emp_id)
            if emp and emp.user_id:
                _create_hrms_notification(
                    emp.user_id,
                    'Payslip available',
                    f'Your JO/COS payslip is ready for {period}.',
                    link_url=url_for('routes.payroll_jo_cos_my_payslips', year=filters['year']),
                    related_type='jo_cos_payslip',
                    related_id=slip_id,
                )
        except Exception:
            # Do not block payslip generation if notification fails for one row.
            pass
    db.session.commit()
    return count


def _can_view_jo_cos_payslip(slip: JoCosPayslip) -> bool:
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role in ('admin', 'hr'):
        return True
    emp = _current_employee_for_user()
    return emp is not None and emp.id == slip.employee_id


def _plantilla_payslip_period_label(year: int, month: int, quincena: str) -> str:
    month_name = date(year, month, 1).strftime('%B %Y')
    q_label = '1st' if quincena == '1' else '2nd'
    return f'{month_name} — {q_label} quincena'


def _can_view_plantilla_payslip(slip: PlantillaPayslip) -> bool:
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role in ('admin', 'hr'):
        return True
    emp = _current_employee_for_user()
    return emp is not None and emp.id == slip.employee_id


JO_COS_PAYSLIPS_PER_A4_PAGE = 4


def _jo_cos_payslip_cell_bounds(page_w: float, page_h: float, slot: int) -> tuple[float, float, float, float]:
    """Return (x_left, y_bottom, x_right, y_top) for a 2×2 grid slot (0–3) on A4."""
    margin = 12
    gap = 8
    cell_w = (page_w - 2 * margin - gap) / 2
    cell_h = (page_h - 2 * margin - gap) / 2
    col = slot % 2
    row = slot // 2  # 0 = top row, 1 = bottom row
    x_left = margin + col * (cell_w + gap)
    x_right = x_left + cell_w
    if row == 0:
        y_bottom = margin + cell_h + gap
        y_top = page_h - margin
    else:
        y_bottom = margin
        y_top = margin + cell_h
    return x_left, y_bottom, x_right, y_top


def _jo_cos_draw_payslip_on_canvas(
    c,
    data: dict,
    x_left: float,
    y_bottom: float,
    x_right: float,
    y_top: float,
) -> None:
    pad = 5
    inner_l = x_left + pad
    inner_r = x_right - pad
    cx = (x_left + x_right) / 2
    box_w = x_right - x_left
    box_h = y_top - y_bottom

    c.setLineWidth(0.4)
    c.rect(x_left, y_bottom, box_w, box_h)

    def _clip(text: str, max_len: int) -> str:
        text = str(text or '')
        return text if len(text) <= max_len else text[: max_len - 1] + '…'

    y = y_top - 11
    c.setFont('Helvetica-Bold', 7)
    c.drawCentredString(cx, y, 'MUNICIPALITY OF JAGNA')
    y -= 9
    c.setFont('Helvetica-Bold', 6.5)
    c.drawCentredString(cx, y, 'PAYSLIP — DAILY WAGE (JO/COS)')
    y -= 10
    c.setFont('Helvetica', 5.5)
    c.drawString(inner_l, y, f"Name: {_clip(data.get('employee_name', ''), 28)}")
    c.drawRightString(inner_r, y, f"No.: {_clip(data.get('employee_no', ''), 12)}")
    y -= 8
    c.drawString(inner_l, y, f"Desig.: {_clip(data.get('designation', ''), 22)}")
    y -= 8
    c.drawString(inner_l, y, f"Dept.: {_clip(data.get('department_name', ''), 24)}")
    c.drawRightString(inner_r, y, _clip(data.get('period_label', ''), 22))
    y -= 8
    c.drawString(inner_l, y, 'Payment: ATM')
    y -= 9
    c.line(inner_l, y, inner_r, y)
    y -= 8
    c.setFont('Helvetica-Bold', 5.5)
    c.drawString(inner_l, y, 'Earnings')
    c.drawRightString(inner_r, y, 'Amount')
    y -= 7
    c.setFont('Helvetica', 5.5)
    earn = (
        f"Days: {data.get('days_worked_display', '')} · "
        f"Rate: {data.get('rate_per_day_display', '')}"
    )
    c.drawString(inner_l + 4, y, _clip(earn, 38))
    c.drawRightString(inner_r, y, data.get('gross_salary_display', ''))
    y -= 8
    c.setFont('Helvetica-Bold', 5.5)
    c.drawString(inner_l, y, 'Deductions')
    y -= 7
    c.setFont('Helvetica', 5.5)
    late_h = data.get('late_hours_display') or ''
    late_label = f"Late{(' (' + late_h + 'h)') if late_h else ''}"
    c.drawString(inner_l + 4, y, late_label)
    c.drawRightString(inner_r, y, data.get('late_deduction_display', '') or '—')
    y -= 7
    c.drawString(inner_l + 4, y, 'HDMF')
    c.drawRightString(inner_r, y, data.get('hdmf_display', '') or '—')
    y -= 8
    c.line(inner_l, y, inner_r, y)
    y -= 8
    c.setFont('Helvetica-Bold', 6.5)
    c.drawString(inner_l, y, 'NET PAY')
    c.drawRightString(inner_r, y, data.get('net_amount_display', ''))
    y -= 10
    c.setFont('Helvetica', 5)
    c.drawString(inner_l, y, 'I acknowledge receipt of the net amount above.')
    sig_w = min(120, (inner_r - inner_l) * 0.55)
    c.line(inner_l, y - 10, inner_l + sig_w, y - 10)
    c.drawString(inner_l, y - 17, 'Signature / date')


def _jo_cos_payslip_pdf_bytes(slips: list[JoCosPayslip]) -> io.BytesIO:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    view_rows = [_jo_cos_payslip_view_dict(s) for s in slips]
    per_page = JO_COS_PAYSLIPS_PER_A4_PAGE
    for i, data in enumerate(view_rows):
        if i > 0 and i % per_page == 0:
            c.showPage()
        slot = i % per_page
        x_left, y_bottom, x_right, y_top = _jo_cos_payslip_cell_bounds(page_w, page_h, slot)
        _jo_cos_draw_payslip_on_canvas(c, data, x_left, y_bottom, x_right, y_top)
    c.save()
    buf.seek(0)
    return buf


PLANTILLA_PAYROLL_DEDUCTION_COLUMNS = (
    ('gsis_mpl', 'MPL'),
    ('gsis_mpl_lite', 'MPL LITE'),
    ('gsis_ps', 'PS'),
    ('gsis_gs', 'GS'),
    ('gsis_cpl', 'CPL'),
    ('hdmf', 'HDMF'),
    ('gsis_emrgyln', 'EMRG LN'),
    ('gsis_educ', 'EDUC LN'),
    ('withholding_tax', 'W/TAX'),
    ('gsis_plreg', 'POLICY'),
    ('gsis_consoloan', 'CONSO'),
    ('gsis_gfal', 'GFAL'),
    ('hdmf_loan', 'HDMF LN'),
    ('lbp', 'LBP'),
    ('jofemco', 'JOFEMCO'),
)

PLANTILLA_PAYROLL_DEDUCTION_SECTIONS = (
    {
        'label': 'GSIS',
        'parts': (
            {'type': 'column', 'key': 'gsis_ps', 'label': 'PS'},
            {
                'type': 'loan_group',
                'group_label': 'Loans',
                'columns': (
                    ('gsis_consoloan', 'Conso'),
                    ('gsis_emrgyln', 'Emgncy'),
                    ('gsis_plreg', 'RegPolicy'),
                    ('gsis_gfal', 'GFAL'),
                    ('gsis_mpl', 'MPL'),
                    ('gsis_cpl', 'CPL'),
                    ('gsis_mpl_lite', 'MPL Lite'),
                ),
            },
        ),
    },
    {
        'label': 'HDMF',
        'parts': (
            {'type': 'column', 'key': 'hdmf_ps', 'label': 'PS'},
            {'type': 'column', 'key': 'hdmf_mp2', 'label': 'MP2'},
            {
                'type': 'loan_group',
                'group_label': 'Loans',
                'columns': (
                    ('hdmf_loan_salary', 'Salary'),
                    ('hdmf_loan_mpl', 'MPL'),
                    ('hdmf_loan_housing', 'Housing'),
                    ('hdmf_loan_safe', 'SAFE'),
                ),
            },
        ),
    },
    {
        'label': 'PHIC',
        'parts': (
            {'type': 'column', 'key': 'phic_ps', 'label': 'PS'},
        ),
    },
    {
        'label': 'LBP',
        'parts': (
            {'type': 'column', 'key': 'lbp', 'label': 'Loan'},
        ),
    },
    {
        'label': 'JOFEMCO',
        'parts': (
            {'type': 'column', 'key': 'jofemco_loan', 'label': 'Loan'},
            {'type': 'column', 'key': 'jofemco_canteen', 'label': 'Canteen'},
        ),
    },
    {
        'label': 'W/Tax',
        'standalone_header': True,
        'parts': (
            {'type': 'column', 'key': 'withholding_tax', 'label': ''},
        ),
    },
    {
        'label': 'LWOP',
        'parts': (
            {'type': 'column', 'key': 'lwop', 'label': 'Deduction'},
        ),
    },
)

PLANTILLA_PAYROLL_GOV_SHARE_SECTIONS = (
    {
        'label': 'GSIS',
        'parts': (
            {'type': 'column', 'key': 'gov_gsis_gs', 'label': 'GS', 'display_quincena': '2'},
            {'type': 'column', 'key': 'gov_gsis_ec', 'label': 'EC', 'display_quincena': '1'},
        ),
    },
    {
        'label': 'HDMF',
        'parts': (
            {'type': 'column', 'key': 'gov_hdmf_gs', 'label': 'GS', 'display_quincena_rule': 'hdmf_gs'},
        ),
    },
    {
        'label': 'PHIC',
        'parts': (
            {'type': 'column', 'key': 'gov_phic_gs', 'label': 'GS', 'display_quincena': '2'},
        ),
    },
)

HDMF_GS_Q1_DEPARTMENT_NAMES = frozenset({'SB', 'MHO'})


def _plantilla_payroll_gov_share_display_keys() -> list[str]:
    keys: list[str] = []
    for section in PLANTILLA_PAYROLL_GOV_SHARE_SECTIONS:
        for part in section['parts']:
            if part['type'] == 'column':
                keys.append(part['key'])
    return keys


def _is_hdmf_gs_q1_department(department_name: str) -> bool:
    name = (department_name or '').strip().upper()
    if not name:
        return False
    if name in HDMF_GS_Q1_DEPARTMENT_NAMES:
        return True
    for token in HDMF_GS_Q1_DEPARTMENT_NAMES:
        if name.startswith(f'{token} ') or name.endswith(f' {token}') or f' {token} ' in f' {name} ':
            return True
    return False


def _plantilla_hdmf_gs_display_quincena(department_name: str) -> str:
    return '1' if _is_hdmf_gs_q1_department(department_name) else '2'


def _plantilla_gov_share_part_display_quincena(part: dict, department_name: str) -> str | None:
    rule = part.get('display_quincena_rule')
    if rule == 'hdmf_gs':
        return _plantilla_hdmf_gs_display_quincena(department_name)
    return part.get('display_quincena')


def _plantilla_employee_gov_share_scheduled_for_quincena(
    quincena: str,
    key: str,
    emp_id: int,
    ded_maps: dict,
    department_name: str,
) -> bool:
    if key == 'gov_gsis_gs':
        if quincena != '2':
            return False
        contrib = _plantilla_contrib_for_quincena(ded_maps['gsis_contrib'].get(emp_id), '2')
        return contrib is not None and _money_decimal(contrib.gs_amount) > 0

    if key == 'gov_gsis_ec':
        if quincena != '1':
            return False
        loan_rec = ded_maps['gsis_loan_rec'].get(emp_id)
        return loan_rec is not None and _money_decimal(loan_rec.ec_amount) > 0

    if key == 'gov_hdmf_gs':
        if quincena != _plantilla_hdmf_gs_display_quincena(department_name):
            return False
        contrib = ded_maps['hdmf_contrib'].get(emp_id)
        return contrib is not None and _money_decimal(contrib.gs_amount) > 0

    if key == 'gov_phic_gs':
        if quincena != '2':
            return False
        phic_by_q = ded_maps['phic'].get(emp_id) or {}
        phic_row = _plantilla_contrib_for_quincena(phic_by_q, '2')
        return phic_row is not None and _money_decimal(phic_row.gs_amount) > 0

    return False


def _plantilla_gov_share_amounts_for_employee(
    quincena: str,
    emp_id: int,
    ded_maps: dict,
    department_name: str,
) -> dict[str, Decimal]:
    amounts = {key: Decimal('0.00') for key in _plantilla_payroll_gov_share_display_keys()}

    if quincena == '2':
        gsis_contrib = _plantilla_contrib_for_quincena(ded_maps['gsis_contrib'].get(emp_id), '2')
        if gsis_contrib is not None:
            amounts['gov_gsis_gs'] = _money_decimal(gsis_contrib.gs_amount)
        phic_row = _plantilla_contrib_for_quincena(ded_maps['phic'].get(emp_id), '2')
        if phic_row is not None:
            amounts['gov_phic_gs'] = _money_decimal(phic_row.gs_amount)

    if quincena == '1':
        loan_rec = ded_maps['gsis_loan_rec'].get(emp_id)
        if loan_rec is not None:
            amounts['gov_gsis_ec'] = _money_decimal(loan_rec.ec_amount)

    if quincena == _plantilla_hdmf_gs_display_quincena(department_name):
        hdmf_contrib = ded_maps['hdmf_contrib'].get(emp_id)
        if hdmf_contrib is not None:
            amounts['gov_hdmf_gs'] = _money_decimal(hdmf_contrib.gs_amount)

    return amounts


def _plantilla_payroll_active_gov_share_keys(
    quincena: str,
    emp_ids: list[int],
    ded_maps: dict,
    department_name: str,
) -> list[str]:
    active: list[str] = []
    for section in PLANTILLA_PAYROLL_GOV_SHARE_SECTIONS:
        for part in section['parts']:
            if part['type'] != 'column':
                continue
            display_q = _plantilla_gov_share_part_display_quincena(part, department_name)
            if display_q != quincena:
                continue
            key = part['key']
            if any(
                _plantilla_employee_gov_share_scheduled_for_quincena(
                    quincena, key, emp_id, ded_maps, department_name,
                )
                for emp_id in emp_ids
            ):
                active.append(key)
    return active


def _plantilla_payroll_gov_share_sections_for_keys(active_keys: set[str]) -> list[dict]:
    sections: list[dict] = []
    for section in PLANTILLA_PAYROLL_GOV_SHARE_SECTIONS:
        parts: list[dict] = []
        for part in section['parts']:
            if part['type'] == 'column' and part['key'] in active_keys:
                parts.append(part)
        if parts:
            sections.append({'label': section['label'], 'parts': tuple(parts)})
    return sections


def _plantilla_payroll_gov_share_section_colspans(sections: list[dict]) -> list[dict]:
    out = []
    for section in sections:
        colspan = sum(1 for part in section['parts'] if part['type'] == 'column')
        out.append({'label': section['label'], 'colspan': colspan, 'parts': section['parts']})
    return out


def _plantilla_payroll_display_deduction_keys() -> list[str]:
    keys: list[str] = []
    for section in PLANTILLA_PAYROLL_DEDUCTION_SECTIONS:
        for part in section['parts']:
            if part['type'] == 'column':
                keys.append(part['key'])
            elif part['type'] == 'loan_group':
                keys.extend(k for k, _ in part['columns'])
    return keys


def _plantilla_payroll_deduction_key_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    for section in PLANTILLA_PAYROLL_DEDUCTION_SECTIONS:
        for part in section['parts']:
            if part['type'] == 'column':
                labels[part['key']] = part['label'] or section['label']
            elif part['type'] == 'loan_group':
                for key, label in part['columns']:
                    labels[key] = label
    return labels


def _plantilla_payroll_sections_for_keys(active_keys: set[str]) -> list[dict]:
    sections: list[dict] = []
    for section in PLANTILLA_PAYROLL_DEDUCTION_SECTIONS:
        parts: list[dict] = []
        for part in section['parts']:
            if part['type'] == 'column':
                if part['key'] in active_keys:
                    parts.append(part)
            elif part['type'] == 'loan_group':
                cols = tuple((k, lbl) for k, lbl in part['columns'] if k in active_keys)
                if cols:
                    parts.append({**part, 'columns': cols})
        if parts:
            entry = {'label': section['label'], 'parts': tuple(parts)}
            if section.get('standalone_header'):
                entry['standalone_header'] = True
            sections.append(entry)
    return sections


def _plantilla_payroll_deduction_section_colspans(sections: list[dict] | None = None) -> list[dict]:
    source = sections if sections is not None else PLANTILLA_PAYROLL_DEDUCTION_SECTIONS
    out = []
    for section in source:
        colspan = 0
        for part in section['parts']:
            if part['type'] == 'column':
                colspan += 1
            elif part['type'] == 'loan_group':
                colspan += len(part['columns'])
        entry = {'label': section['label'], 'colspan': colspan, 'parts': section['parts']}
        if section.get('standalone_header'):
            entry['standalone_header'] = True
        out.append(entry)
    return out


def _payroll_deduction_quincena_scheduled(ded, quincena: str) -> bool:
    if ded is None:
        return False
    if quincena == '1':
        return bool(ded.q1_enabled)
    return bool(ded.q2_enabled)


def _gsis_loan_ded_matches_field(ded: GsisLoanDeduction, loan_field: str) -> bool:
    key = (ded.loan_type or '').strip().lower()
    return key == loan_field or key == loan_field.upper()


def _plantilla_employee_deduction_scheduled_for_quincena(
    quincena: str,
    key: str,
    emp_id: int,
    ded_maps: dict,
) -> bool:
    if key == 'gsis_ps':
        contrib = _plantilla_contrib_for_quincena(ded_maps['gsis_contrib'].get(emp_id), quincena)
        return contrib is not None

    if key == 'phic_ps':
        phic = _plantilla_contrib_for_quincena(ded_maps['phic'].get(emp_id), quincena)
        return phic is not None

    if key == 'hdmf_ps':
        contrib = ded_maps['hdmf_contrib'].get(emp_id)
        return (
            contrib is not None
            and str(quincena) == str(contrib.deductible_quincena)
            and _money_decimal(contrib.ps_amount) > 0
        )

    if key == 'hdmf_mp2':
        contrib = ded_maps['hdmf_contrib'].get(emp_id)
        return (
            contrib is not None
            and str(quincena) == str(contrib.deductible_quincena)
            and _money_decimal(contrib.mp2_amount) > 0
        )

    if key in _GSIS_LOAN_FIELD_BY_DED_KEY:
        loan_field = _GSIS_LOAN_FIELD_BY_DED_KEY[key]
        loan_deds = ded_maps['gsis_loan_deds'].get(emp_id, [])
        for ded in loan_deds:
            if _gsis_loan_ded_matches_field(ded, loan_field):
                return _payroll_deduction_quincena_scheduled(ded, quincena)
        loan_rec = ded_maps['gsis_loan_rec'].get(emp_id)
        if loan_rec is not None:
            q1, q2 = _gsis_loan_quincena_split(_money_decimal(getattr(loan_rec, loan_field, 0)))
            return (q1 > 0 if quincena == '1' else q2 > 0)
        return False

    if key.startswith('hdmf_loan_'):
        loan_type = key.replace('hdmf_loan_', '', 1)
        loan_deds = ded_maps['hdmf_loan_deds'].get(emp_id, [])
        for ded in loan_deds:
            if (ded.loan_type or '').strip().lower() == loan_type:
                return _payroll_deduction_quincena_scheduled(ded, quincena)
        loan_rec = ded_maps['hdmf_loan_recs'].get(emp_id)
        if loan_rec is not None and not loan_deds:
            month_amt = _money_decimal(getattr(loan_rec, loan_type, 0))
            q1, q2 = _gsis_loan_quincena_split(month_amt)
            return (q1 > 0 if quincena == '1' else q2 > 0)
        return False

    if key == 'lbp':
        ded = ded_maps['lbp'].get(emp_id)
        if _payroll_deduction_quincena_scheduled(ded, quincena):
            return True
        rec = ded_maps['lbp_rec'].get(emp_id)
        if rec is None:
            return False
        amt = rec.q1_amount if quincena == '1' else rec.q2_amount
        return _money_decimal(amt) > 0

    if key in ('jofemco_loan', 'jofemco_canteen'):
        ded = ded_maps['jofemco'].get(emp_id)
        rec = ded_maps['jofemco_rec'].get(emp_id)
        if rec is not None:
            if key == 'jofemco_loan':
                amt = rec.loan_q1 if quincena == '1' else rec.loan_q2
            else:
                amt = rec.canteen_q1 if quincena == '1' else rec.canteen_q2
            if _money_decimal(amt) > 0:
                return True
        if ded is not None and _payroll_deduction_quincena_scheduled(ded, quincena):
            return _money_decimal(ded.q1_amount if quincena == '1' else ded.q2_amount) > 0
        return False

    if key == 'withholding_tax':
        if quincena != '1':
            return False
        if _payroll_deduction_quincena_scheduled(ded_maps['wht'].get(emp_id), quincena):
            return True
        rec = ded_maps['wht_rec'].get(emp_id)
        return rec is not None and _money_decimal(rec.q1_amount) > 0

    return False


def _plantilla_payroll_active_deduction_keys(
    quincena: str,
    emp_ids: list[int],
    ded_maps: dict,
) -> list[str]:
    active: list[str] = []
    for key in _plantilla_payroll_display_deduction_keys():
        if any(
            _plantilla_employee_deduction_scheduled_for_quincena(quincena, key, emp_id, ded_maps)
            for emp_id in emp_ids
        ):
            active.append(key)
    return active


def _plantilla_payroll_layout_for_quincena(
    quincena: str,
    emp_ids: list[int],
    ded_maps: dict,
    department_name: str = '',
    employees: list | None = None,
) -> dict:
    active_keys = _plantilla_payroll_active_deduction_keys(quincena, emp_ids, ded_maps)
    if quincena == '1' and employees and any_lwop_payroll_deduction(employees):
        if 'lwop' not in active_keys:
            active_keys.append('lwop')
    active_set = set(active_keys)
    sections = _plantilla_payroll_sections_for_keys(active_set)
    gov_active_keys = _plantilla_payroll_active_gov_share_keys(
        quincena, emp_ids, ded_maps, department_name,
    )
    gov_sections = _plantilla_payroll_gov_share_sections_for_keys(set(gov_active_keys))
    return {
        'display_keys': active_keys,
        'deduction_sections': _plantilla_payroll_deduction_section_colspans(sections),
        'deduction_col_count': len(active_keys),
        'deduction_key_labels': _plantilla_payroll_deduction_key_labels(),
        'gov_share_keys': gov_active_keys,
        'gov_share_sections': _plantilla_payroll_gov_share_section_colspans(gov_sections),
        'gov_share_col_count': len(gov_active_keys),
    }


def _plantilla_payroll_empty_layout() -> dict:
    return {
        'display_keys': [],
        'deduction_sections': [],
        'deduction_col_count': 0,
        'deduction_key_labels': _plantilla_payroll_deduction_key_labels(),
        'gov_share_keys': [],
        'gov_share_sections': [],
        'gov_share_col_count': 0,
    }

_GSIS_LOAN_FIELD_BY_DED_KEY = {
    'gsis_mpl': 'mpl',
    'gsis_mpl_lite': 'mpl_lite',
    'gsis_cpl': 'cpl',
    'gsis_emrgyln': 'emrgyln',
    'gsis_educ': 'educ',
    'gsis_plreg': 'plreg',
    'gsis_consoloan': 'consoloan',
    'gsis_gfal': 'gfal',
}


def _is_plantilla_payroll_employee(emp: Employee) -> bool:
    return not _is_jo_cos_employee(emp)


def _plantilla_employee_designation(emp: Employee) -> str:
    return (emp.position or '').strip() or '—'


def _plantilla_payroll_service_period_label(year: int, month: int, quincena: str) -> str:
    start_d, end_d = _quincena_date_range(year, month, quincena)
    return f'{start_d.strftime("%B %d, %Y")} to {end_d.strftime("%B %d, %Y")}'


def _plantilla_payroll_period_label(filters: dict, dept_name: str | None = None) -> str:
    month_name = date(filters['year'], filters['month'], 1).strftime('%B %Y')
    q_label = '1st' if filters['quincena'] == '1' else '2nd'
    parts = [f'{month_name} — {q_label} quincena']
    if dept_name:
        parts.append(dept_name)
    return ' · '.join(parts)


def _payroll_quincena_split_amount(month_amount, quincena: str) -> Decimal:
    q1, q2 = _gsis_loan_quincena_split(month_amount)
    return q1 if quincena == '1' else q2


def _payroll_scheduled_quincena_amount(
    ded,
    quincena: str,
    fallback_month_amount: Decimal | None = None,
) -> Decimal:
    if ded is not None:
        if quincena == '1':
            return _money_decimal(ded.q1_amount) if ded.q1_enabled else Decimal('0.00')
        return _money_decimal(ded.q2_amount) if ded.q2_enabled else Decimal('0.00')
    if fallback_month_amount is not None:
        return _payroll_quincena_split_amount(fallback_month_amount, quincena)
    return Decimal('0.00')


def _plantilla_contrib_for_quincena(contrib_by_q: dict | None, quincena: str):
    if not contrib_by_q:
        return None
    return contrib_by_q.get(str(quincena))


def _plantilla_payroll_deduction_maps(
    emp_ids: list[int],
    year: int,
    month: int,
    quincena: str,
) -> dict:
    maps: dict = {
        'gsis_contrib': {},
        'gsis_loan_rec': {},
        'gsis_loan_deds': {},
        'hdmf_contrib': {},
        'hdmf_contrib_recs': {},
        'hdmf_loan_deds': {},
        'hdmf_loan_recs': {},
        'lbp': {},
        'lbp_rec': {},
        'jofemco': {},
        'jofemco_rec': {},
        'wht': {},
        'wht_rec': {},
        'phic': {},
    }
    if not emp_ids:
        return maps
    for row in GsisContribution.query.filter(
        GsisContribution.employee_id.in_(emp_ids),
        GsisContribution.year == year,
        GsisContribution.month == month,
    ).all():
        maps['gsis_contrib'].setdefault(row.employee_id, {})[str(row.deductible_quincena)] = row
    for row in GsisLoanRecord.query.filter(
        GsisLoanRecord.employee_id.in_(emp_ids),
        GsisLoanRecord.year == year,
        GsisLoanRecord.month == month,
    ).all():
        maps['gsis_loan_rec'][row.employee_id] = row
    for row in GsisLoanDeduction.query.filter(
        GsisLoanDeduction.employee_id.in_(emp_ids),
        GsisLoanDeduction.year == year,
        GsisLoanDeduction.month == month,
    ).all():
        maps['gsis_loan_deds'].setdefault(row.employee_id, []).append(row)
    for row in HdmfContributionDeduction.query.filter(
        HdmfContributionDeduction.employee_id.in_(emp_ids),
        HdmfContributionDeduction.year == year,
        HdmfContributionDeduction.month == month,
        HdmfContributionDeduction.employment_scope == 'plantilla',
    ).all():
        maps['hdmf_contrib'][row.employee_id] = row
    for row in HdmfContributionRecord.query.filter(
        HdmfContributionRecord.employee_id.in_(emp_ids),
        HdmfContributionRecord.year == year,
        HdmfContributionRecord.month == month,
        HdmfContributionRecord.employment_scope == 'plantilla',
    ).all():
        maps['hdmf_contrib_recs'].setdefault(row.employee_id, []).append(row)
    for row in HdmfLoanDeduction.query.filter(
        HdmfLoanDeduction.employee_id.in_(emp_ids),
        HdmfLoanDeduction.year == year,
        HdmfLoanDeduction.month == month,
        HdmfLoanDeduction.employment_scope == 'plantilla',
    ).all():
        maps['hdmf_loan_deds'].setdefault(row.employee_id, []).append(row)
    for row in HdmfLoanRecord.query.filter(
        HdmfLoanRecord.employee_id.in_(emp_ids),
        HdmfLoanRecord.year == year,
        HdmfLoanRecord.month == month,
        HdmfLoanRecord.employment_scope == 'plantilla',
    ).all():
        maps['hdmf_loan_recs'][row.employee_id] = row
    for row in LbpBillingDeduction.query.filter(
        LbpBillingDeduction.employee_id.in_(emp_ids),
        LbpBillingDeduction.year == year,
        LbpBillingDeduction.month == month,
    ).all():
        maps['lbp'][row.employee_id] = row
    for row in LbpBillingRecord.query.filter(
        LbpBillingRecord.employee_id.in_(emp_ids),
        LbpBillingRecord.year == year,
        LbpBillingRecord.month == month,
    ).all():
        maps['lbp_rec'][row.employee_id] = row
    for row in JofemcoBillingDeduction.query.filter(
        JofemcoBillingDeduction.employee_id.in_(emp_ids),
        JofemcoBillingDeduction.year == year,
        JofemcoBillingDeduction.month == month,
    ).all():
        maps['jofemco'][row.employee_id] = row
    for row in JofemcoBillingRecord.query.filter(
        JofemcoBillingRecord.employee_id.in_(emp_ids),
        JofemcoBillingRecord.year == year,
        JofemcoBillingRecord.month == month,
    ).all():
        maps['jofemco_rec'][row.employee_id] = row
    for row in WithholdingTaxDeduction.query.filter(
        WithholdingTaxDeduction.employee_id.in_(emp_ids),
        WithholdingTaxDeduction.year == year,
        WithholdingTaxDeduction.month == month,
    ).all():
        maps['wht'][row.employee_id] = row
    for row in WithholdingTaxRecord.query.filter(
        WithholdingTaxRecord.employee_id.in_(emp_ids),
        WithholdingTaxRecord.year == year,
        WithholdingTaxRecord.month == month,
    ).all():
        maps['wht_rec'][row.employee_id] = row
    for row in PhicContribution.query.filter(
        PhicContribution.employee_id.in_(emp_ids),
        PhicContribution.year == year,
        PhicContribution.month == month,
    ).all():
        maps['phic'].setdefault(row.employee_id, {})[str(row.deductible_quincena)] = row
    return maps


def _plantilla_gsis_loan_quincena_amount(
    emp_id: int,
    loan_field: str,
    quincena: str,
    loan_rec: GsisLoanRecord | None,
    loan_deds: list[GsisLoanDeduction],
) -> Decimal:
    for ded in loan_deds:
        key = (ded.loan_type or '').strip().lower()
        if key == loan_field or key == loan_field.upper():
            return _payroll_scheduled_quincena_amount(ded, quincena)
    if loan_rec is not None:
        return _payroll_quincena_split_amount(_money_decimal(getattr(loan_rec, loan_field, 0)), quincena)
    return Decimal('0.00')


def _plantilla_gsis_ps_for_quincena(
    quincena: str,
    gsis_contrib: GsisContribution | None,
) -> Decimal:
    if gsis_contrib is None or str(quincena) != str(gsis_contrib.deductible_quincena):
        return Decimal('0.00')
    return _money_decimal(gsis_contrib.ps_amount)


def _plantilla_hdmf_ps_mp2_for_quincena(
    quincena: str,
    hdmf_contrib: HdmfContributionDeduction | None,
) -> tuple[Decimal, Decimal]:
    if hdmf_contrib is None or str(quincena) != str(hdmf_contrib.deductible_quincena):
        return Decimal('0.00'), Decimal('0.00')
    return _money_decimal(hdmf_contrib.ps_amount), _money_decimal(hdmf_contrib.mp2_amount)


def _plantilla_hdmf_loan_by_type_for_quincena(
    quincena: str,
    loan_deds: list[HdmfLoanDeduction],
    loan_rec: HdmfLoanRecord | None,
) -> dict[str, Decimal]:
    out = {f'hdmf_loan_{field}': Decimal('0.00') for field, _ in HDMF_LOAN_TYPE_FIELDS}
    for ded in loan_deds:
        lt = (ded.loan_type or '').strip().lower()
        key = f'hdmf_loan_{lt}'
        if key in out:
            out[key] = _payroll_scheduled_quincena_amount(ded, quincena)
    if loan_rec is not None and not loan_deds:
        for field, _ in HDMF_LOAN_TYPE_FIELDS:
            month_amt = _money_decimal(getattr(loan_rec, field, 0))
            out[f'hdmf_loan_{field}'] = _payroll_quincena_split_amount(month_amt, quincena)
    return out


def _plantilla_phic_ps_for_quincena(
    quincena: str,
    phic_row: PhicContribution | None,
) -> Decimal:
    if phic_row is None or str(quincena) != str(phic_row.deductible_quincena):
        return Decimal('0.00')
    return _money_decimal(phic_row.ps_amount)


def _plantilla_lbp_quincena_amount(
    quincena: str,
    ded: LbpBillingDeduction | None,
    rec: LbpBillingRecord | None,
) -> Decimal:
    amt = _payroll_scheduled_quincena_amount(ded, quincena)
    if amt > 0 or ded is not None:
        return amt
    if rec is None:
        return Decimal('0.00')
    return _money_decimal(rec.q1_amount if quincena == '1' else rec.q2_amount)


def _plantilla_jofemco_loan_canteen_for_quincena(
    quincena: str,
    ded: JofemcoBillingDeduction | None,
    rec: JofemcoBillingRecord | None,
) -> tuple[Decimal, Decimal]:
    if rec is not None:
        if quincena == '1':
            loan = _money_decimal(rec.loan_q1)
            canteen = _money_decimal(rec.canteen_q1)
        else:
            loan = _money_decimal(rec.loan_q2)
            canteen = _money_decimal(rec.canteen_q2)
    else:
        loan = canteen = Decimal('0.00')

    if ded is not None:
        enabled = ded.q1_enabled if quincena == '1' else ded.q2_enabled
        if not enabled:
            return Decimal('0.00'), Decimal('0.00')
        scheduled = _money_decimal(ded.q1_amount if quincena == '1' else ded.q2_amount)
        upload_total = (loan + canteen).quantize(Decimal('0.01'))
        if upload_total > 0:
            if scheduled != upload_total:
                loan = (loan * scheduled / upload_total).quantize(Decimal('0.01'))
                canteen = (scheduled - loan).quantize(Decimal('0.01'))
        elif scheduled > 0:
            loan = scheduled
            canteen = Decimal('0.00')
    elif rec is None:
        return Decimal('0.00'), Decimal('0.00')

    return loan.quantize(Decimal('0.01')), canteen.quantize(Decimal('0.01'))


def _plantilla_wht_quincena_amount(
    quincena: str,
    ded: WithholdingTaxDeduction | None,
    rec: WithholdingTaxRecord | None,
) -> Decimal:
    if quincena != '1':
        return Decimal('0.00')
    amt = _payroll_scheduled_quincena_amount(ded, quincena)
    if amt > 0 or ded is not None:
        return amt
    if rec is None:
        return Decimal('0.00')
    return _money_decimal(rec.q1_amount)


def _plantilla_payroll_row_for_employee(
    emp: Employee,
    row_no: int,
    filters: dict,
    ded_maps: dict,
    display_keys: list[str],
    gov_share_keys: list[str],
) -> dict:
    year, month, quincena = filters['year'], filters['month'], filters['quincena']
    monthly_raw = _current_salary_amount(emp)
    monthly = _money_decimal(monthly_raw or 0)
    accrued = (monthly / Decimal('2')).quantize(Decimal('0.01'))

    gsis_contrib = _plantilla_contrib_for_quincena(ded_maps['gsis_contrib'].get(emp.id), quincena)
    gsis_ps = _plantilla_gsis_ps_for_quincena(quincena, gsis_contrib)

    loan_rec = ded_maps['gsis_loan_rec'].get(emp.id)
    loan_deds = ded_maps['gsis_loan_deds'].get(emp.id, [])
    hdmf_ps, hdmf_mp2 = _plantilla_hdmf_ps_mp2_for_quincena(
        quincena,
        ded_maps['hdmf_contrib'].get(emp.id),
    )
    hdmf_loans = _plantilla_hdmf_loan_by_type_for_quincena(
        quincena,
        ded_maps['hdmf_loan_deds'].get(emp.id, []),
        ded_maps['hdmf_loan_recs'].get(emp.id),
    )
    phic_ps = _plantilla_phic_ps_for_quincena(
        quincena, _plantilla_contrib_for_quincena(ded_maps['phic'].get(emp.id), quincena),
    )
    jofemco_loan, jofemco_canteen = _plantilla_jofemco_loan_canteen_for_quincena(
        quincena, ded_maps['jofemco'].get(emp.id), ded_maps['jofemco_rec'].get(emp.id),
    )
    lwop_days = Decimal('0')
    lwop_amount = Decimal('0.00')
    if quincena == '1':
        lwop_days, lwop_amount = preview_lwop_payroll_deduction(emp)
    deductions: dict[str, Decimal] = {
        'gsis_ps': gsis_ps,
        'hdmf_ps': hdmf_ps,
        'hdmf_mp2': hdmf_mp2,
        'phic_ps': phic_ps,
        'lbp': _plantilla_lbp_quincena_amount(
            quincena, ded_maps['lbp'].get(emp.id), ded_maps['lbp_rec'].get(emp.id),
        ),
        'jofemco_loan': jofemco_loan,
        'jofemco_canteen': jofemco_canteen,
        'withholding_tax': _plantilla_wht_quincena_amount(
            quincena, ded_maps['wht'].get(emp.id), ded_maps['wht_rec'].get(emp.id),
        ),
        'lwop': lwop_amount,
    }
    deductions.update(hdmf_loans)
    for ded_key, loan_field in _GSIS_LOAN_FIELD_BY_DED_KEY.items():
        if loan_field == 'educ':
            continue
        deductions[ded_key] = _plantilla_gsis_loan_quincena_amount(
            emp.id, loan_field, quincena, loan_rec, loan_deds,
        )

    display_keys = list(display_keys)
    total_ded = sum(
        (deductions.get(k, Decimal('0.00')) for k in display_keys),
        Decimal('0.00'),
    ).quantize(Decimal('0.01'))

    # Legacy combined keys for PDF export
    deductions['gsis_gs'] = Decimal('0.00')
    deductions['gsis_educ'] = Decimal('0.00')
    deductions['hdmf'] = (hdmf_ps + hdmf_mp2).quantize(Decimal('0.01'))
    deductions['hdmf_loan'] = sum(hdmf_loans.values(), Decimal('0.00')).quantize(Decimal('0.01'))
    deductions['jofemco'] = (jofemco_loan + jofemco_canteen).quantize(Decimal('0.01'))
    net = (accrued - total_ded).quantize(Decimal('0.01'))
    department_name = (emp.department.name if emp.department else '')
    gov_share = _plantilla_gov_share_amounts_for_employee(
        quincena, emp.id, ded_maps, department_name,
    )

    name = emp.directory_name_mi

    row = {
        'row_no': row_no,
        'employee_pk': emp.id,
        'employee_no': emp.employee_id or '',
        'employee_name': name,
        'designation': _plantilla_employee_designation(emp),
        'department_name': (emp.department.name if emp.department else ''),
        'monthly_rate': monthly,
        'monthly_rate_display': _money_str(monthly) if monthly_raw is not None else '—',
        'accrued': accrued,
        'accrued_display': _money_str(accrued),
        'deductions': deductions,
        'total_deductions': total_ded,
        'total_deductions_display': _money_str(total_ded) if total_ded else '',
        'net_amount': net,
        'net_amount_display': _money_str(net),
        'gov_share': gov_share,
        'search_text': f'{name} {_plantilla_employee_designation(emp)}'.lower(),
        'lwop_days': lwop_days,
        'lwop_days_display': f'{lwop_days:.3f}'.rstrip('0').rstrip('.') if lwop_days else '',
    }
    for key in display_keys:
        amt = deductions.get(key, Decimal('0.00'))
        row[f'ded_{key}'] = amt
        row[f'ded_{key}_display'] = _money_str_or_blank(amt)
    for key in gov_share_keys:
        amt = gov_share.get(key, Decimal('0.00'))
        row[f'gov_{key}'] = amt
        row[f'gov_{key}_display'] = _money_str_or_blank(amt)
    for key, _label in PLANTILLA_PAYROLL_DEDUCTION_COLUMNS:
        if f'ded_{key}' in row:
            continue
        amt = deductions.get(key, Decimal('0.00'))
        row[f'ded_{key}'] = amt
        row[f'ded_{key}_display'] = _money_str_or_blank(amt)
    return row


def _plantilla_payroll_rows(filters: dict) -> tuple[list[dict], dict, dict]:
    try:
        dept_id = int(filters['department_id'])
    except (TypeError, ValueError):
        empty_totals = {
            'monthly_rate': Decimal('0'),
            'accrued': Decimal('0'),
            'net_amount': Decimal('0'),
        }
        layout = _plantilla_payroll_empty_layout()
        for key in layout['display_keys']:
            empty_totals[f'ded_{key}'] = Decimal('0')
        for key in layout['gov_share_keys']:
            empty_totals[f'gov_{key}'] = Decimal('0')
        for key, _ in PLANTILLA_PAYROLL_DEDUCTION_COLUMNS:
            if f'ded_{key}' not in empty_totals:
                empty_totals[f'ded_{key}'] = Decimal('0')
        return [], empty_totals, layout

    employees = (
        Employee.query.options(db.joinedload(Employee.department))
        .filter_by(status='active', department_id=dept_id)
        .order_by(Employee.last_name.asc(), Employee.first_name.asc())
        .all()
    )
    employees = [e for e in employees if _is_plantilla_payroll_employee(e)]
    year, month, quincena = filters['year'], filters['month'], filters['quincena']
    emp_ids = [e.id for e in employees]
    ded_maps = _plantilla_payroll_deduction_maps(emp_ids, year, month, quincena)
    department_name = ''
    if employees and employees[0].department:
        department_name = employees[0].department.name or ''
    layout = _plantilla_payroll_layout_for_quincena(
        quincena, emp_ids, ded_maps, department_name,
        employees=employees,
    )
    display_keys = layout['display_keys']
    gov_share_keys = layout['gov_share_keys']

    rows = []
    totals = {
        'monthly_rate': Decimal('0'),
        'accrued': Decimal('0'),
        'net_amount': Decimal('0'),
    }
    for key in display_keys:
        totals[f'ded_{key}'] = Decimal('0')
    for key in gov_share_keys:
        totals[f'gov_{key}'] = Decimal('0')
    for key, _ in PLANTILLA_PAYROLL_DEDUCTION_COLUMNS:
        if f'ded_{key}' not in totals:
            totals[f'ded_{key}'] = Decimal('0')

    for idx, emp in enumerate(employees, start=1):
        row = _plantilla_payroll_row_for_employee(
            emp, idx, filters, ded_maps, display_keys, gov_share_keys,
        )
        rows.append(row)
        totals['monthly_rate'] += row['monthly_rate']
        totals['accrued'] += row['accrued']
        totals['net_amount'] += row['net_amount']
        for key in display_keys:
            totals[f'ded_{key}'] += row['deductions'].get(key, Decimal('0.00'))
        for key in gov_share_keys:
            totals[f'gov_{key}'] += row['gov_share'].get(key, Decimal('0.00'))
        for key, _ in PLANTILLA_PAYROLL_DEDUCTION_COLUMNS:
            if key in display_keys:
                continue
            totals[f'ded_{key}'] += row['deductions'].get(key, Decimal('0.00'))

    for key in totals:
        totals[key] = totals[key].quantize(Decimal('0.01'))
    totals_display = {
        k: (
            _money_str_or_blank(v)
            if k.startswith('ded_') or k.startswith('gov_')
            else _money_str(v)
        )
        for k, v in totals.items()
    }
    totals_display['monthly_rate'] = _money_str(totals['monthly_rate'])
    totals_display['accrued'] = _money_str(totals['accrued'])
    totals_display['net_amount'] = _money_str(totals['net_amount'])
    return rows, totals_display, layout


def _plantilla_payroll_gov_share_key_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    for section in PLANTILLA_PAYROLL_GOV_SHARE_SECTIONS:
        for part in section['parts']:
            if part['type'] == 'column':
                labels[part['key']] = f"{section['label']} {part['label']}"
    return labels


def _plantilla_payroll_pdf_bytes(
    rows: list[dict],
    totals: dict,
    service_period: str,
    department_name: str,
    pay_date: date,
    display_keys: list[str] | None = None,
    key_labels: dict[str, str] | None = None,
    gov_share_keys: list[str] | None = None,
    gov_share_labels: dict[str, str] | None = None,
) -> io.BytesIO:
    from reportlab.lib.pagesizes import legal, landscape
    from reportlab.pdfgen import canvas

    page_size = landscape(legal)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size)
    width, height = page_size
    margin = 24
    labels_map = key_labels or _plantilla_payroll_deduction_key_labels()
    ded_keys = display_keys if display_keys is not None else [k for k, _ in PLANTILLA_PAYROLL_DEDUCTION_COLUMNS]
    ded_cols = [(key, labels_map.get(key, key)) for key in ded_keys]
    gov_labels_map = gov_share_labels or _plantilla_payroll_gov_share_key_labels()
    gov_keys = gov_share_keys if gov_share_keys is not None else []
    gov_cols = [(key, gov_labels_map.get(key, key)) for key in gov_keys]
    col_w = [
        14, 88, 62, 42, 42,
        *([28] * len(ded_cols)),
        38,
        *([22] * len(gov_cols)),
        14,
    ]
    table_w = sum(col_w)
    x0 = margin
    row_h = 9
    font_hdr = 5
    font_body = 4.5

    def draw_page_header(y_top: float) -> float:
        y = y_top
        c.setFont('Helvetica-Bold', 9)
        c.drawCentredString(width / 2, y, 'Republic of the Philippines')
        y -= 10
        c.setFont('Helvetica', 8)
        c.drawCentredString(width / 2, y, 'Province of Bohol')
        y -= 10
        c.drawCentredString(width / 2, y, 'Municipality of Jagna')
        y -= 12
        c.setFont('Helvetica-Bold', 10)
        c.drawCentredString(width / 2, y, 'MUNICIPAL PAYROLL')
        y -= 12
        c.setFont('Helvetica', 6.5)
        c.drawString(x0, y, f'PERIOD OF SERVICE : {service_period}')
        c.drawRightString(width - margin, y, f'Date {pay_date.strftime("%B %d, %Y")}')
        y -= 10
        c.setFont('Helvetica-Bold', 7)
        c.drawString(x0, y, department_name.upper())
        return y - 6

    def draw_table_header(y: float) -> float:
        c.setFont('Helvetica-Bold', font_hdr)
        xs = [x0]
        for w in col_w[:-1]:
            xs.append(xs[-1] + w)
        labels = (
            ['NO', 'NAME OF EMPLOYEE', 'DESIGNATION', 'MONTHLY\nRATE', 'ACCRUED']
            + [lbl for _k, lbl in ded_cols]
            + ['PAID']
            + [lbl for _k, lbl in gov_cols]
            + ['NO']
        )
        for i, lbl in enumerate(labels):
            c.drawCentredString(xs[i] + col_w[i] / 2, y, lbl.replace('\n', ' '))
        c.line(x0, y - 2, x0 + table_w, y - 2)
        return y - row_h

    def draw_row(y: float, r: dict, bold: bool = False) -> float:
        c.setFont('Helvetica-Bold' if bold else 'Helvetica', font_body)
        xs = [x0]
        for w in col_w[:-1]:
            xs.append(xs[-1] + w)
        vals = [
            str(r.get('row_no', '')),
            str(r.get('employee_name', ''))[:24],
            str(r.get('designation', ''))[:14],
            r.get('monthly_rate_display', ''),
            r.get('accrued_display', ''),
        ]
        for key, _ in ded_cols:
            vals.append(r.get(f'ded_{key}_display', ''))
        vals.append(r.get('net_amount_display', ''))
        for key, _ in gov_cols:
            vals.append(r.get(f'gov_{key}_display', ''))
        vals.append(str(r.get('row_no', '')))
        for i, val in enumerate(vals):
            if i in (0, len(vals) - 1):
                c.drawCentredString(xs[i] + col_w[i] / 2, y, val)
            elif i >= 3:
                c.drawRightString(xs[i] + col_w[i] - 2, y, val)
            else:
                c.drawString(xs[i] + 2, y, val)
        return y - row_h

    y = height - margin
    y = draw_page_header(y)
    y = draw_table_header(y)
    for r in rows:
        if y < 120:
            c.showPage()
            y = draw_page_header(height - margin)
            y = draw_table_header(y)
        y = draw_row(y, r)
    totals_row = {
        'row_no': '',
        'employee_name': 'TOTAL',
        'designation': '',
        'monthly_rate_display': totals.get('monthly_rate', ''),
        'accrued_display': totals.get('accrued', ''),
        'net_amount_display': totals.get('net_amount', ''),
    }
    for key, _ in ded_cols:
        totals_row[f'ded_{key}_display'] = totals.get(f'ded_{key}', '')
    for key, _ in gov_cols:
        totals_row[f'gov_{key}_display'] = totals.get(f'gov_{key}', '')
    c.line(x0, y + 4, x0 + table_w, y + 4)
    y = draw_row(y, totals_row, bold=True)

    sig_y = 52
    c.setFont('Helvetica', 5)
    c.drawString(x0, sig_y + 28, '(1) I HEREBY CERTIFY on my official oath that the above payroll is correct.')
    c.line(x0, sig_y + 14, x0 + 150, sig_y + 14)
    c.drawString(x0, sig_y, 'Municipal Mayor')
    c.drawCentredString(width / 2, sig_y, 'Disbursing Officer')
    c.line(width / 2 - 70, sig_y + 14, width / 2 + 70, sig_y + 14)
    c.drawRightString(width - margin, sig_y + 28, f'Total net: {totals.get("net_amount", "")}')
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _dtr_regen_summary_department_totals(year: int, month: int, quincena: str) -> list[dict]:
    summaries = (DtrQuincenaWorktimeSummary.query
                   .filter_by(year=year, month=month, quincena_half=quincena)
                   .all())
    by_dept: dict[int | None, int] = {}
    for s in summaries:
        key = s.department_id
        by_dept[key] = by_dept.get(key, 0) + int(s.net_rendered_mins or 0)
    dept_ids = [k for k in by_dept if k is not None]
    names = {}
    if dept_ids:
        for d in Department.query.filter(Department.id.in_(dept_ids)).all():
            names[d.id] = d.name or f'Department #{d.id}'
    rows = []
    for dept_id in sorted(by_dept, key=lambda x: (x is None, x or 0)):
        net_mins = by_dept[dept_id]
        if dept_id is None:
            label = '(No department)'
        else:
            label = names.get(dept_id, f'Department #{dept_id}')
        rows.append({
            'department_id': dept_id,
            'department_name': label,
            'net_rendered_mins': net_mins,
            'net_rendered': _mins_to_hhmm(net_mins),
        })
    rows.sort(key=lambda r: r['department_name'].lower())
    return rows


def _dtr_regen_summary_employee_rows(year: int, month: int, quincena: str) -> list[dict]:
    summaries = (DtrQuincenaWorktimeSummary.query
                 .filter_by(year=year, month=month, quincena_half=quincena)
                 .all())
    out = []
    for s in summaries:
        emp = s.employee
        dept = emp.department if emp else None
        dept_name = dept.name if dept else '(No department)'
        gross = int(s.gross_work_mins or 0)
        late = int(s.late_mins or 0)
        ut = int(s.undertime_mins or 0)
        abm = int(s.absence_mins or 0)
        net = int(s.net_rendered_mins or 0)
        code = (emp.employee_id or '') if emp else ''
        name = emp.directory_name if emp else ''
        out.append({
            'department_name': dept_name,
            'employee_code': code,
            'employee_name': name,
            'gross_work_mins': gross,
            'gross_work': _mins_to_hhmm(gross),
            'late_mins': late,
            'late': _mins_to_hhmm(late),
            'undertime_mins': ut,
            'undertime': _mins_to_hhmm(ut),
            'absence_mins': abm,
            'absence': _mins_to_hhmm(abm),
            'absence_days': int(s.absence_days or 0),
            'net_rendered_mins': net,
            'net_rendered': _mins_to_hhmm(net),
        })
    out.sort(key=lambda r: (r['department_name'].lower(), r['employee_name'].lower()))
    return out


def _dtr_regen_summary_csv_bytes(dept_rows: list[dict], emp_rows: list[dict], period_label: str) -> bytes:
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['DTR quincena worktime summary', period_label])
    w.writerow([])
    w.writerow(['Department totals'])
    w.writerow(['Department', 'Net worktime rendered (HH:MM)', 'Net minutes'])
    for r in dept_rows:
        w.writerow([r.get('department_name', ''), r.get('net_rendered', ''), r.get('net_rendered_mins', 0)])
    w.writerow([])
    w.writerow(['By employee'])
    w.writerow(['Department', 'Employee ID', 'Employee name', 'Gross work', 'Late', 'Undertime', 'Absence', 'Absence days', 'Net rendered'])
    for r in emp_rows:
        w.writerow([
            r.get('department_name', ''),
            r.get('employee_code', ''),
            r.get('employee_name', ''),
            r.get('gross_work', ''),
            r.get('late', ''),
            r.get('undertime', ''),
            r.get('absence', ''),
            r.get('absence_days', 0),
            r.get('net_rendered', ''),
        ])
    return output.getvalue().encode('utf-8')


def _dtr_regen_summary_pdf_bytes(dept_rows: list[dict], emp_rows: list[dict], period_label: str) -> io.BytesIO:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    y = height - 50
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, y, 'DTR quincena worktime summary')
    y -= 16
    c.setFont('Helvetica', 10)
    c.drawString(40, y, period_label)
    y -= 22
    c.setFont('Helvetica-Bold', 10)
    c.drawString(40, y, 'Department totals (net worktime rendered)')
    y -= 14
    c.setFont('Helvetica-Bold', 8)
    c.drawString(40, y, 'Department')
    c.drawString(320, y, 'Net HH:MM')
    y -= 12
    c.setFont('Helvetica', 8)
    for r in dept_rows:
        if y < 60:
            c.showPage()
            y = height - 50
            c.setFont('Helvetica', 8)
        c.drawString(40, y, str(r.get('department_name', ''))[:55])
        c.drawString(320, y, str(r.get('net_rendered', '')))
        y -= 11
    y -= 8
    c.setFont('Helvetica-Bold', 10)
    c.drawString(40, y, 'Employees')
    y -= 14
    c.setFont('Helvetica-Bold', 7)
    c.drawString(40, y, 'Dept')
    c.drawString(130, y, 'Name')
    c.drawString(300, y, 'Gross')
    c.drawString(340, y, 'Late')
    c.drawString(375, y, 'UT')
    c.drawString(405, y, 'Abs')
    c.drawString(445, y, 'Net')
    y -= 11
    c.setFont('Helvetica', 7)
    for r in emp_rows:
        if y < 50:
            c.showPage()
            y = height - 50
            c.setFont('Helvetica', 7)
        c.drawString(40, y, str(r.get('department_name', ''))[:18])
        c.drawString(130, y, f"{r.get('employee_name', '')[:22]} ({r.get('employee_code', '')})")
        c.drawString(300, y, str(r.get('gross_work', '')))
        c.drawString(340, y, str(r.get('late', '')))
        c.drawString(375, y, str(r.get('undertime', '')))
        c.drawString(405, y, str(r.get('absence', '')))
        c.drawString(445, y, str(r.get('net_rendered', '')))
        y -= 10
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _run_dtr_quincena_regeneration(year: int, month: int, quincena: str, created_by_user_id: int | None) -> int:
    """Process quincena worktime. Returns count of overnight Out punches auto-moved."""
    start_d, end_d = _quincena_date_range(year, month, quincena)
    tag = _dtr_regen_tag(year, month, quincena)

    DtrQuincenaWorktimeSummary.query.filter_by(year=year, month=month, quincena_half=quincena).delete(synchronize_session=False)
    WorkHours.query.filter(
        WorkHours.work_date >= start_d,
        WorkHours.work_date <= end_d,
    ).delete(synchronize_session=False)
    JoCosOvertime.query.filter_by(year=year, month=month, quincena_half=quincena).delete(synchronize_session=False)
    prev_jo_cos_ledger_emp_ids = [
        r[0] for r in db.session.query(JoCosOvertimeLedger.employee_id)
        .filter(JoCosOvertimeLedger.regen_tag == tag, JoCosOvertimeLedger.entry_type == 'earned')
        .distinct().all()
    ]
    JoCosOvertimeLedger.query.filter(
        JoCosOvertimeLedger.regen_tag == tag,
        JoCosOvertimeLedger.entry_type == 'earned',
    ).delete(synchronize_session=False)

    prev_emp_ids = [r[0] for r in db.session.query(LeaveLedger.employee_id).filter(LeaveLedger.remarks == tag).distinct().all()]
    lwop_affected = delete_lwop_regen_rows_for_quincena(start_d, end_d)
    LeaveLedger.query.filter(LeaveLedger.remarks == tag).delete(synchronize_session=False)
    db.session.flush()
    for eid in set(prev_emp_ids) | set(lwop_affected):
        recompute_leave_ledger_balances(eid)

    employees = (Employee.query.filter_by(status='active')
                 .order_by(Employee.last_name, Employee.first_name)
                 .all())
    processed_at = datetime.utcnow()
    plantilla_recompute_ids: list[int] = []

    # Restore actual In/Out (and clear leftover 8-5 / 7-6 stamps) before overnight pairing.
    restore_actual_dtr_punches(start_d, end_d, employees)
    db.session.flush()

    # Auto-move early-morning Out onto overnight flexi start days before worktime calc.
    from app.dtr_overnight_punch_service import pair_overnight_punches_for_quincena
    _pair_moves, pair_n = pair_overnight_punches_for_quincena(
        year=year,
        month=month,
        quincena_half=quincena,
        start_d=start_d,
        end_d=end_d,
        employees=employees,
        duty_schedule_from_row=_duty_schedule_from_flexible_worktime_row,
        entry_date_bounds=_flexible_worktime_entry_date_bounds,
        employee_on_flexi=_employee_on_flexible_worktime_quincena,
        dry_run=False,
    )
    if pair_n:
        db.session.flush()

    for emp in employees:
        totals = _aggregate_employee_dtr_worktime(
            emp, start_d, end_d, persist_row_undertime=True,
            quincena_year=year, quincena_month=month, quincena_half=quincena,
        )
        gross = totals['gross']
        late_tot = totals['late']
        ut_tot = totals['undertime']
        absence_mins = totals['absence_mins']
        absence_days = totals['absence_days']
        net = totals['net']

        plantilla_ledger = (emp.status_of_appointment or '').strip() in LEAVE_CREDITS_STATUSES and not totals['jo_cos']
        if plantilla_ledger and (late_tot > 0 or ut_tot > 0):
            particulars = f'DTR quincena regen VL deductions — {year:04d}-{month:02d} Q{quincena}'
            db.session.add(LeaveLedger(
                employee_id=emp.id,
                transaction_date=end_d,
                particulars=particulars,
                vl_tardiness=minutes_to_day_equivalent(late_tot),
                vl_undertime=minutes_to_day_equivalent(ut_tot),
                remarks=tag,
                created_by=created_by_user_id,
            ))
            plantilla_recompute_ids.append(emp.id)

        if plantilla_ledger and post_lwop_for_absent_days(
            emp, totals['day_rows'], created_by_user_id=created_by_user_id,
        ):
            plantilla_recompute_ids.append(emp.id)

        if plantilla_ledger:
            cto_mins = _overtime_authorization_cto_minutes_for_employee(
                emp.id, year, month, quincena, start_d, end_d
            )
            if cto_mins > 0:
                db.session.add(LeaveLedger(
                    employee_id=emp.id,
                    transaction_date=end_d,
                    particulars=f'DTR quincena regen CTO earned — {year:04d}-{month:02d} Q{quincena}',
                    cto_earned=minutes_to_day_equivalent(cto_mins),
                    remarks=tag,
                    created_by=created_by_user_id,
                ))
                plantilla_recompute_ids.append(emp.id)

        db.session.add(DtrQuincenaWorktimeSummary(
            employee_id=emp.id,
            department_id=emp.department_id,
            year=year,
            month=month,
            quincena_half=quincena,
            gross_work_mins=gross,
            late_mins=late_tot,
            undertime_mins=ut_tot,
            absence_mins=absence_mins,
            absence_days=absence_days,
            net_rendered_mins=net,
            processed_at=processed_at,
        ))
        for day in totals['day_rows']:
            db.session.add(WorkHours(
                employee_id=emp.id,
                department_id=emp.department_id,
                work_date=day['work_date'],
                year=year,
                month=month,
                quincena_half=quincena,
                gross_work_mins=day['gross_work_mins'],
                late_mins=day['late_mins'],
                undertime_mins=day['undertime_mins'],
                absence_mins=day['absence_mins'],
                net_rendered_mins=day['net_rendered_mins'],
                remarks=day['remarks'],
                processed_at=processed_at,
            ))
        if totals['jo_cos']:
            for ot_day in _jo_cos_overtime_day_rows_for_employee(
                emp.id, year, month, quincena, start_d, end_d
            ):
                if ot_day['overtime_mins'] > 0:
                    db.session.add(JoCosOvertime(
                        employee_id=emp.id,
                        department_id=emp.department_id,
                        work_date=ot_day['work_date'],
                        year=year,
                        month=month,
                        quincena_half=quincena,
                        overtime_mins=ot_day['overtime_mins'],
                        processed_at=processed_at,
                    ))
            db.session.flush()
            _sync_jo_cos_overtime_earned_ledger_for_quincena(
                emp.id, year, month, quincena, end_d, tag, created_by_user_id,
            )

    db.session.flush()
    for eid in set(plantilla_recompute_ids):
        recompute_leave_ledger_balances(eid)
    for eid in set(prev_jo_cos_ledger_emp_ids):
        _recompute_jo_cos_overtime_ledger_balances(eid)
    db.session.commit()
    return int(pair_n or 0)


def _parse_flexible_worktime_rest_dates(
    raw,
    idx: int,
    entry_min: date,
    entry_max: date,
) -> list[date]:
    """Parse optional rest_dates JSON (YYYY-MM-DD list) for one Flexible Worktime row."""
    if raw in (None, '', []):
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(';', ',').split(',') if p.strip()]
        raw = parts
    if not isinstance(raw, list):
        raise ValueError(f'Row {idx}: rest_dates must be a list of YYYY-MM-DD dates.')
    seen: set[date] = set()
    out: list[date] = []
    for item in raw:
        try:
            d = datetime.strptime(str(item).strip()[:10], '%Y-%m-%d').date()
        except ValueError as e:
            raise ValueError(f'Row {idx}: invalid rest date {item!r}.') from e
        if d < entry_min or d > entry_max:
            raise ValueError(
                f'Row {idx}: rest date {d.isoformat()} must fall within the selected quincena '
                f'(or one day before/after for night shift).'
            )
        if d not in seen:
            seen.add(d)
            out.append(d)
    return sorted(out)


def _flexible_worktime_entry_date_pairs(
    item: dict,
    idx: int,
    entry_min: date,
    entry_max: date,
    duty_mode: str,
) -> list[tuple[date, date]]:
    """
    Date ranges for one JSON entry.

    Rest rows may send ``rest_dates`` (several non-consecutive days in the quincena);
    each date becomes a one-day rest range. Otherwise Date from / Date to is used.
    """
    rest_dates: list[date] = []
    if duty_mode == 'rest':
        rest_dates = _parse_flexible_worktime_rest_dates(
            item.get('rest_dates'), idx, entry_min, entry_max
        )
    if rest_dates:
        return [(d, d) for d in rest_dates]
    try:
        d0 = datetime.strptime((item.get('date_start') or '').strip(), '%Y-%m-%d').date()
        d1 = datetime.strptime((item.get('date_end') or '').strip(), '%Y-%m-%d').date()
    except ValueError as e:
        raise ValueError(
            f'Row {idx}: invalid date range. For rest days, pick dates on the quincena calendar '
            f'or enter Date from and Date to.'
        ) from e
    if d0 > d1:
        raise ValueError(f'Row {idx}: start date must be on or before end date.')
    if d0 < entry_min or d1 > entry_max:
        raise ValueError(
            f'Row {idx}: dates must fall within the selected quincena '
            f'(or one day before/after for night shift).'
        )
    return [(d0, d1)]


def _parse_flexible_worktime_entries_json(
    raw: str,
    start_d: date,
    end_d: date,
    *,
    allowed_department_id: int | None = None,
) -> list[dict]:
    raw = (raw or '').strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError('Invalid flexible worktime list JSON.') from e
    if not isinstance(data, list):
        raise ValueError('Flexible worktime list must be a JSON array.')
    # Night-shift entries may start/end one day outside the core quincena.
    entry_min, entry_max = _flexible_worktime_entry_date_bounds(start_d, end_d)
    parsed: list[dict] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'Row {idx}: invalid entry.')
        try:
            emp_id = int(item.get('employee_id'))
        except (TypeError, ValueError) as e:
            raise ValueError(f'Row {idx}: select an employee.') from e
        emp = Employee.query.get(emp_id)
        if not emp or (emp.status or '').strip().lower() != 'active':
            raise ValueError(f'Row {idx}: employee not found or not active.')
        if allowed_department_id is not None and emp.department_id != allowed_department_id:
            raise ValueError(
                f'Row {idx}: employee is outside your department. '
                'Payroll Maker may only manage Flexible Worktime for their department.'
            )
        duty_mode = (item.get('duty_mode') or 'split').strip().lower()
        if duty_mode not in ('split', 'straight', 'rest'):
            raise ValueError(f'Row {idx}: duty mode must be split, straight, or rest.')
        date_pairs = _flexible_worktime_entry_date_pairs(
            item, idx, entry_min, entry_max, duty_mode
        )
        if duty_mode == 'rest':
            time_in = None
            time_out = None
            break_out = None
            break_in = None
        else:
            time_in = _parse_flexible_worktime_time(item.get('time_in'))
            time_out = _parse_flexible_worktime_time(item.get('time_out'))
            if not time_in or not time_out:
                raise ValueError(f'Row {idx}: time in and time out are required.')
            if duty_mode == 'straight':
                break_out = None
                break_in = None
            else:
                break_out = _parse_flexible_worktime_time(item.get('break_out'))
                break_in = _parse_flexible_worktime_time(item.get('break_in'))
                if not break_out or not break_in:
                    raise ValueError(f'Row {idx}: break out and break in are required for split duty.')
        for d0, d1 in date_pairs:
            parsed.append({
                'employee_id': emp_id,
                'date_start': d0,
                'date_end': d1,
                'duty_mode': duty_mode,
                'time_in': time_in,
                'break_out': break_out,
                'break_in': break_in,
                'time_out': time_out,
            })
    by_emp: dict[int, list[tuple[date, date]]] = {}
    for row in parsed:
        ranges = by_emp.setdefault(row['employee_id'], [])
        for a0, a1 in ranges:
            if row['date_start'] <= a1 and a0 <= row['date_end']:
                raise ValueError(
                    'Overlapping date ranges for the same employee are not allowed. '
                    'For overnight (e.g. 4:00 pm -> 12:00 am), Date from/to are shift-start days only — '
                    'do not extend Date to into the next morning. Same slot Tue-Thu is OK as one row; '
                    'do not overlap that range with Fri-Sun. Rest days may be several non-consecutive '
                    'dates, but not on a day that already has another Flexible Worktime row.'
                )
        ranges.append((row['date_start'], row['date_end']))
    return parsed


def _save_flexible_worktime_quincena(
    year: int,
    month: int,
    quincena: str,
    entries: list[dict],
    user_id: int | None,
    *,
    department_id: int | None = None,
) -> None:
    """
    Replace Flexible Worktime rows for a quincena.

    When ``department_id`` is set (Payroll Maker scope), only that department's
    existing rows are removed/replaced so other departments are preserved.
    """
    q = FlexibleWorktime.query.filter_by(
        year=year, month=month, quincena_half=quincena
    )
    if department_id is not None:
        scoped_employee_ids = (
            db.session.query(Employee.id)
            .filter(Employee.department_id == department_id)
        )
        q = q.filter(FlexibleWorktime.employee_id.in_(scoped_employee_ids))
    q.delete(synchronize_session=False)
    now = datetime.utcnow()
    for row in entries:
        db.session.add(FlexibleWorktime(
            employee_id=row['employee_id'],
            year=year,
            month=month,
            quincena_half=quincena,
            date_start=row['date_start'],
            date_end=row['date_end'],
            duty_mode=row.get('duty_mode') or 'split',
            time_in=row['time_in'],
            break_out=row.get('break_out'),
            break_in=row.get('break_in'),
            time_out=row['time_out'],
            created_by_user_id=user_id,
            created_at=now,
            updated_at=now,
        ))
    db.session.commit()


def _save_overtime_authorization_quincena(
    year: int, month: int, quincena: str, entries: list[dict], user_id: int | None
) -> None:
    OvertimeAuthorization.query.filter_by(
        year=year, month=month, quincena_half=quincena
    ).delete(synchronize_session=False)
    now = datetime.utcnow()
    for row in entries:
        db.session.add(OvertimeAuthorization(
            employee_id=row['employee_id'],
            year=year,
            month=month,
            quincena_half=quincena,
            date_start=row['date_start'],
            date_end=row['date_end'],
            time_in=row['time_in'],
            break_out=row['break_out'],
            break_in=row['break_in'],
            time_out=row['time_out'],
            created_by_user_id=user_id,
            created_at=now,
            updated_at=now,
        ))
    db.session.commit()


def _save_jo_cos_extend_service_quincena(
    year: int,
    month: int,
    quincena: str,
    entries: list[dict],
    user_id: int | None,
    *,
    department_id: int | None = None,
) -> None:
    q = JoCosExtendService.query.filter_by(
        year=year, month=month, quincena_half=quincena
    )
    if department_id is not None:
        scoped_employee_ids = (
            db.session.query(Employee.id)
            .filter(Employee.department_id == department_id)
        )
        q = q.filter(JoCosExtendService.employee_id.in_(scoped_employee_ids))
    q.delete(synchronize_session=False)
    now = datetime.utcnow()
    for row in entries:
        db.session.add(JoCosExtendService(
            employee_id=row['employee_id'],
            year=year,
            month=month,
            quincena_half=quincena,
            date_start=row['date_start'],
            date_end=row['date_end'],
            time_in=row['time_in'],
            break_out=row['break_out'],
            break_in=row['break_in'],
            time_out=row['time_out'],
            created_by_user_id=user_id,
            created_at=now,
            updated_at=now,
        ))
    db.session.commit()


@bp.route('/dtr/flexible-worktime', methods=['GET', 'POST'])
@login_required
def dtr_flexible_worktime():
    denied = _require_flexible_worktime_manager()
    if denied:
        return denied

    maker_dept_id = _payroll_maker_department_id()
    if (
        (getattr(current_user, 'role', None) or '').strip().lower() == 'payroll_maker'
        and maker_dept_id is None
    ):
        flash(
            'Access denied. Payroll Maker must be linked to an employee with a department '
            'to manage Flexible Worktime.',
            'error',
        )
        return redirect(url_for(_dashboard_for_user(current_user)))

    filters = _flexible_worktime_parse_filters(request.args if request.method == 'GET' else request.form)
    # Payroll Maker is locked to their department (cannot browse/save other depts).
    if maker_dept_id is not None:
        filters['department_id'] = str(maker_dept_id)

    start_d, end_d = _quincena_date_range(filters['year'], filters['month'], filters['quincena'])
    entry_min, entry_max = _flexible_worktime_entry_date_bounds(start_d, end_d)
    if request.method == 'POST':
        try:
            entries = _parse_flexible_worktime_entries_json(
                request.form.get('entries_json', ''),
                start_d,
                end_d,
                allowed_department_id=maker_dept_id,
            )
            _save_flexible_worktime_quincena(
                filters['year'], filters['month'], filters['quincena'],
                entries, getattr(current_user, 'id', None),
                department_id=maker_dept_id,
            )
            flash('Flexible worktime saved for this quincena.', 'success')
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'error')
        return redirect(url_for(
            'routes.dtr_flexible_worktime',
            year=filters['year'], month=filters['month'], quincena=filters['quincena'],
            **({'department_id': filters['department_id']} if filters.get('department_id') else {}),
        ))

    rows = (
        FlexibleWorktime.query.filter_by(
            year=filters['year'], month=filters['month'], quincena_half=filters['quincena']
        )
        .join(Employee, FlexibleWorktime.employee_id == Employee.id)
        .order_by(Employee.last_name.asc(), Employee.first_name.asc(), FlexibleWorktime.date_start.asc())
        .all()
    )
    dept_filter_id = None
    if filters.get('department_id'):
        try:
            dept_filter_id = int(filters['department_id'])
            rows = [r for r in rows if r.employee and r.employee.department_id == dept_filter_id]
        except ValueError:
            dept_filter_id = None
    entries = [_flexible_worktime_row_dict(r) for r in rows]
    employees = (
        Employee.query.filter_by(status='active')
        .order_by(Employee.last_name.asc(), Employee.first_name.asc())
        .all()
    )
    if dept_filter_id is not None:
        employees = [e for e in employees if e.department_id == dept_filter_id]
    employees_for_js = [
        {
            'id': e.id,
            'employee_id': e.employee_id or '',
            'first_name': e.first_name or '',
            'last_name': e.last_name or '',
            'department_id': e.department_id,
            'directory_name': e.directory_name,
            'label': f"{e.directory_name} ({e.employee_id})",
        }
        for e in employees
    ]
    if maker_dept_id is not None:
        departments = Department.query.filter_by(id=maker_dept_id).order_by(Department.name.asc()).all()
    else:
        departments = Department.query.order_by(Department.name.asc()).all()
    flexi_shift_presets = [
        {
            'label': 'Day shift (8 am – 5 pm)',
            'duty_mode': 'split',
            'time_in': '08:00', 'break_out': '12:00', 'break_in': '13:00', 'time_out': '17:00',
        },
        {
            'label': 'Straight 8h (8 am – 4 pm)',
            'duty_mode': 'straight',
            'time_in': '08:00', 'break_out': '', 'break_in': '', 'time_out': '16:00',
        },
        {
            'label': 'Night shift — MDRRMO (8 pm – 5 am)',
            'duty_mode': 'split',
            'time_in': '20:00', 'break_out': '00:00', 'break_in': '00:30', 'time_out': '05:00',
        },
        {
            'label': 'Rest day',
            'duty_mode': 'rest',
            'time_in': '', 'break_out': '', 'break_in': '', 'time_out': '',
        },
    ]
    period_label = (
        f"{date(filters['year'], filters['month'], 1).strftime('%B %Y')} — "
        f"{'1st' if filters['quincena'] == '1' else '2nd'} quincena "
        f"({start_d.isoformat()} to {end_d.isoformat()})"
    )
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'dtr_flexible_worktime.html',
        filters=filters,
        entries=entries,
        entries_json=json.dumps(entries),
        employees_for_js=employees_for_js,
        period_label=period_label,
        quincena_start=start_d.isoformat(),
        quincena_end=end_d.isoformat(),
        date_entry_min=entry_min.isoformat(),
        date_entry_max=entry_max.isoformat(),
        departments=departments,
        flexi_shift_presets=flexi_shift_presets,
        employee=employee,
        is_payroll_maker_scope=maker_dept_id is not None,
        can_regenerate_dtr=_user_is_admin_or_hr(),
    )


@bp.route('/dtr/overtime-authorization', methods=['GET', 'POST'])
@login_required
def dtr_overtime_authorization():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _flexible_worktime_parse_filters(request.args if request.method == 'GET' else request.form)
    start_d, end_d = _quincena_date_range(filters['year'], filters['month'], filters['quincena'])
    if request.method == 'POST':
        try:
            entries = _parse_quincena_schedule_entries_json(
                request.form.get('entries_json', ''), start_d, end_d, require_plantilla=True
            )
            _save_overtime_authorization_quincena(
                filters['year'], filters['month'], filters['quincena'],
                entries, getattr(current_user, 'id', None),
            )
            flash('Overtime authorization saved for this quincena.', 'success')
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'error')
        return redirect(url_for(
            'routes.dtr_overtime_authorization',
            year=filters['year'], month=filters['month'], quincena=filters['quincena'],
        ))

    rows = (
        OvertimeAuthorization.query.filter_by(
            year=filters['year'], month=filters['month'], quincena_half=filters['quincena']
        )
        .join(Employee, OvertimeAuthorization.employee_id == Employee.id)
        .order_by(Employee.last_name.asc(), Employee.first_name.asc(), OvertimeAuthorization.date_start.asc())
        .all()
    )
    entries = [_overtime_authorization_row_dict(r) for r in rows]
    employees = (
        Employee.query.filter_by(status='active')
        .filter(Employee.status_of_appointment.in_(LEAVE_CREDITS_STATUSES))
        .order_by(Employee.last_name.asc(), Employee.first_name.asc())
        .all()
    )
    employees = [e for e in employees if _is_plantilla_leave_credits_employee(e)]
    employees_for_js = [
        {
            'id': e.id,
            'employee_id': e.employee_id or '',
            'first_name': e.first_name or '',
            'last_name': e.last_name or '',
            'directory_name': e.directory_name,
            'label': f"{e.directory_name} ({e.employee_id})",
        }
        for e in employees
    ]
    period_label = (
        f"{date(filters['year'], filters['month'], 1).strftime('%B %Y')} — "
        f"{'1st' if filters['quincena'] == '1' else '2nd'} quincena "
        f"({start_d.isoformat()} to {end_d.isoformat()})"
    )
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'dtr_overtime_authorization.html',
        filters=filters,
        entries=entries,
        entries_json=json.dumps(entries),
        employees_for_js=employees_for_js,
        period_label=period_label,
        quincena_start=start_d.isoformat(),
        quincena_end=end_d.isoformat(),
        employee=employee,
    )


@bp.route('/dtr/jo-cos-extend-service', methods=['GET', 'POST'])
@login_required
def dtr_jo_cos_extend_service():
    denied = _require_x_service_manager()
    if denied:
        return denied

    maker_dept_id = _payroll_maker_department_id()
    if (
        (getattr(current_user, 'role', None) or '').strip().lower() == 'payroll_maker'
        and maker_dept_id is None
    ):
        flash(
            'Access denied. Payroll Maker must be linked to an employee with a department '
            'to manage X-Service Authorization.',
            'error',
        )
        return redirect(url_for(_dashboard_for_user(current_user)))

    filters = _flexible_worktime_parse_filters(request.args if request.method == 'GET' else request.form)
    if maker_dept_id is not None:
        filters['department_id'] = str(maker_dept_id)

    start_d, end_d = _quincena_date_range(filters['year'], filters['month'], filters['quincena'])
    if request.method == 'POST':
        try:
            entries = _parse_quincena_schedule_entries_json(
                request.form.get('entries_json', ''),
                start_d,
                end_d,
                require_jo_cos=True,
                allowed_department_id=maker_dept_id,
            )
            _save_jo_cos_extend_service_quincena(
                filters['year'], filters['month'], filters['quincena'],
                entries, getattr(current_user, 'id', None),
                department_id=maker_dept_id,
            )
            flash('X-Service authorization saved for this quincena.', 'success')
        except ValueError as e:
            db.session.rollback()
            flash(str(e), 'error')
        return redirect(url_for(
            'routes.dtr_jo_cos_extend_service',
            year=filters['year'], month=filters['month'], quincena=filters['quincena'],
            **({'department_id': filters['department_id']} if filters.get('department_id') else {}),
        ))

    rows = (
        JoCosExtendService.query.filter_by(
            year=filters['year'], month=filters['month'], quincena_half=filters['quincena']
        )
        .join(Employee, JoCosExtendService.employee_id == Employee.id)
        .order_by(Employee.last_name.asc(), Employee.first_name.asc(), JoCosExtendService.date_start.asc())
        .all()
    )
    dept_filter_id = None
    if filters.get('department_id'):
        try:
            dept_filter_id = int(filters['department_id'])
            rows = [r for r in rows if r.employee and r.employee.department_id == dept_filter_id]
        except ValueError:
            dept_filter_id = None
    entries = [_jo_cos_extend_service_row_dict(r) for r in rows]
    employees = (
        Employee.query.filter_by(status='active')
        .order_by(Employee.last_name.asc(), Employee.first_name.asc())
        .all()
    )
    employees = [e for e in employees if _is_jo_cos_employee(e)]
    if dept_filter_id is not None:
        employees = [e for e in employees if e.department_id == dept_filter_id]
    employees_for_js = [
        {
            'id': e.id,
            'employee_id': e.employee_id or '',
            'first_name': e.first_name or '',
            'last_name': e.last_name or '',
            'directory_name': e.directory_name,
            'label': f"{e.directory_name} ({e.employee_id})",
        }
        for e in employees
    ]
    if maker_dept_id is not None:
        departments = Department.query.filter_by(id=maker_dept_id).order_by(Department.name.asc()).all()
    else:
        departments = Department.query.order_by(Department.name.asc()).all()
    period_label = (
        f"{date(filters['year'], filters['month'], 1).strftime('%B %Y')} — "
        f"{'1st' if filters['quincena'] == '1' else '2nd'} quincena "
        f"({start_d.isoformat()} to {end_d.isoformat()})"
    )
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'dtr_jo_cos_extend_service.html',
        filters=filters,
        entries=entries,
        entries_json=json.dumps(entries),
        employees_for_js=employees_for_js,
        period_label=period_label,
        quincena_start=start_d.isoformat(),
        quincena_end=end_d.isoformat(),
        employee=employee,
        departments=departments,
        is_payroll_maker_scope=maker_dept_id is not None,
        can_regenerate_dtr=_user_is_admin_or_hr(),
    )


@bp.route('/overtime-credits')
@login_required
def jo_cos_overtime_credits():
    emp, denied = _require_jo_cos_employee()
    if denied:
        return denied
    try:
        _backfill_jo_cos_overtime_ledger_for_employee(emp.id)
    except Exception:
        db.session.rollback()
    ledger_rows = _jo_cos_overtime_ledger_display_rows(emp.id)
    pending_requests = (
        JoCosOvertimeOffsetRequest.query.filter_by(employee_id=emp.id, status='pending')
        .order_by(JoCosOvertimeOffsetRequest.created_at.desc())
        .all()
    )
    balance = _jo_cos_overtime_balance_hours(emp.id)
    available = _jo_cos_overtime_available_balance_hours(emp.id)
    return render_template(
        'overtime_credits/view.html',
        employee=emp,
        ledger_rows=ledger_rows,
        pending_requests=pending_requests,
        balance_hours=_format_jo_cos_hours(balance),
        available_hours=_format_jo_cos_hours(available),
    )


@bp.route('/overtime-credits/apply-offset', methods=['POST'])
@login_required
def jo_cos_overtime_apply_offset():
    emp, denied = _require_jo_cos_employee()
    if denied:
        return denied
    try:
        d0 = datetime.strptime((request.form.get('date_start') or '').strip(), '%Y-%m-%d').date()
        d1 = datetime.strptime((request.form.get('date_end') or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        flash('Enter valid inclusive offset dates.', 'error')
        return redirect(url_for('routes.jo_cos_overtime_credits'))
    mode = (request.form.get('offset_mode') or '').strip().upper()
    if mode not in ('FULL', 'AM', 'PM'):
        flash('Select full-day, AM, or PM for the offset.', 'error')
        return redirect(url_for('routes.jo_cos_overtime_credits'))
    if d0 > d1:
        flash('Start date must be on or before end date.', 'error')
        return redirect(url_for('routes.jo_cos_overtime_credits'))
    total = _jo_cos_offset_total_hours(d0, d1, mode)
    if total < Decimal('4'):
        flash('Offset application must be at least 4 hours.', 'error')
        return redirect(url_for('routes.jo_cos_overtime_credits'))
    available = _jo_cos_overtime_available_balance_hours(emp.id)
    if total > available:
        flash(
            f'Insufficient overtime balance. Available: {_format_jo_cos_hours(available)} hour(s); '
            f'requested: {_format_jo_cos_hours(total)} hour(s).',
            'error',
        )
        return redirect(url_for('routes.jo_cos_overtime_credits'))
    reason = (request.form.get('reason') or '').strip() or None
    req = JoCosOvertimeOffsetRequest(
        employee_id=emp.id,
        date_start=d0,
        date_end=d1,
        offset_mode=mode.lower(),
        total_hours=total,
        status='pending',
        reason=reason,
        submitted_by_user_id=getattr(current_user, 'id', None),
    )
    db.session.add(req)
    db.session.flush()
    _notify_jo_cos_offset_submitted(req)
    db.session.commit()
    flash('Overtime offset application submitted for approval.', 'success')
    return redirect(url_for('routes.jo_cos_overtime_credits'))


@bp.route('/overtime-offset-approvals')
@login_required
def jo_cos_overtime_offset_approvals():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('manager', 'admin', 'hr'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    q = (
        JoCosOvertimeOffsetRequest.query.filter_by(status='pending')
        .join(Employee, JoCosOvertimeOffsetRequest.employee_id == Employee.id)
    )
    if role == 'manager':
        dept_ids = _managed_department_ids_for_manager()
        if not dept_ids:
            pending = []
        else:
            pending = (
                q.filter(Employee.department_id.in_(dept_ids))
                .order_by(JoCosOvertimeOffsetRequest.created_at.desc())
                .all()
            )
    else:
        pending = q.order_by(JoCosOvertimeOffsetRequest.created_at.desc()).all()
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'overtime_credits/approvals.html',
        pending=pending,
        employee=employee,
    )


@bp.route('/overtime-offset/<int:id>/approve', methods=['POST'])
@login_required
def jo_cos_overtime_offset_approve(id):
    req = JoCosOvertimeOffsetRequest.query.get_or_404(id)
    if req.status != 'pending':
        flash('This application is no longer pending.', 'error')
        return redirect(url_for('routes.jo_cos_overtime_offset_approvals'))
    if not _can_approve_jo_cos_overtime_offset(req):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    available = _jo_cos_overtime_available_balance_hours(req.employee_id)
    if Decimal(str(req.total_hours or 0)) > available:
        flash('Cannot approve: employee overtime balance is insufficient.', 'error')
        return redirect(url_for('routes.jo_cos_overtime_offset_approvals'))
    req.status = 'approved'
    req.reviewed_by_user_id = getattr(current_user, 'id', None)
    req.reviewed_at = datetime.utcnow()
    db.session.add(JoCosOvertimeLedger(
        employee_id=req.employee_id,
        entry_type='offset',
        transaction_date=req.date_start,
        offset_date_start=req.date_start,
        offset_date_end=req.date_end,
        offset_mode=req.offset_mode,
        offset_hours=req.total_hours,
        balance_hours=Decimal('0'),
        offset_request_id=req.id,
        particulars=f'Overtime offset approved — {req.date_start.isoformat()} to {req.date_end.isoformat()} ({(req.offset_mode or "").upper()})',
        created_by_user_id=getattr(current_user, 'id', None),
    ))
    db.session.flush()
    _recompute_jo_cos_overtime_ledger_balances(req.employee_id)
    emp = req.employee
    if emp and emp.user_id:
        _create_hrms_notification(
            emp.user_id,
            'Overtime offset approved',
            f'Your overtime offset application for {_format_jo_cos_hours(req.total_hours)} hour(s) was approved.',
            link_url=url_for('routes.jo_cos_overtime_credits'),
            related_type='jo_cos_overtime_offset',
            related_id=req.id,
        )
    db.session.commit()
    flash('Overtime offset application approved.', 'success')
    return redirect(url_for('routes.jo_cos_overtime_offset_approvals'))


@bp.route('/overtime-offset/<int:id>/reject', methods=['POST'])
@login_required
def jo_cos_overtime_offset_reject(id):
    req = JoCosOvertimeOffsetRequest.query.get_or_404(id)
    if req.status != 'pending':
        flash('This application is no longer pending.', 'error')
        return redirect(url_for('routes.jo_cos_overtime_offset_approvals'))
    if not _can_approve_jo_cos_overtime_offset(req):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    reason = (request.form.get('rejection_reason') or '').strip() or 'No reason given.'
    req.status = 'rejected'
    req.reviewed_by_user_id = getattr(current_user, 'id', None)
    req.reviewed_at = datetime.utcnow()
    req.rejection_reason = reason
    emp = req.employee
    if emp and emp.user_id:
        _create_hrms_notification(
            emp.user_id,
            'Overtime offset rejected',
            f'Your overtime offset application was rejected. Reason: {reason}',
            link_url=url_for('routes.jo_cos_overtime_credits'),
            related_type='jo_cos_overtime_offset',
            related_id=req.id,
        )
    db.session.commit()
    flash('Overtime offset application rejected.', 'success')
    return redirect(url_for('routes.jo_cos_overtime_offset_approvals'))


@bp.route('/notifications')
@login_required
def notifications_list():
    rows = (
        HrmsNotification.query.filter_by(user_id=current_user.id)
        .order_by(HrmsNotification.created_at.desc())
        .limit(100)
        .all()
    )
    employee = current_user.employee if current_user.employee else None
    return render_template('notifications/list.html', notifications=rows, employee=employee)


@bp.route('/notifications/<int:id>/read', methods=['POST'])
@login_required
def notification_mark_read(id):
    row = HrmsNotification.query.get_or_404(id)
    if row.user_id != current_user.id:
        flash('Access denied.', 'error')
        return redirect(url_for('routes.notifications_list'))
    row.is_read = True
    db.session.commit()
    if row.link_url:
        return redirect(row.link_url)
    return redirect(url_for('routes.notifications_list'))


@bp.route('/dtr/regenerate', methods=['GET', 'POST'])
@login_required
def dtr_regenerate():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    anchor = date.today()
    opts = _build_quincena_upload_options(anchor)
    if request.method == 'GET':
        employee = current_user.employee if current_user.employee else None
        default_quincena = f'{anchor.year:04d}-{anchor.month:02d}-{"1" if anchor.day <= 15 else "2"}'
        return render_template(
            'dtr_regenerate.html',
            quincena_options=opts,
            year=anchor.year,
            month=anchor.month,
            quincena='1',
            default_quincena=default_quincena,
            employee=employee,
        )
    raw_period = (request.form.get('quincena_period') or '').strip()
    if raw_period:
        py, pm, pq = _parse_quincena_upload_value(raw_period)
        if py is None:
            flash('Invalid quincena selection.', 'error')
            return redirect(url_for('routes.dtr_regenerate'))
        year, month, quincena = py, pm, pq
    else:
        month = int((request.form.get('month') or str(anchor.month)).strip())
        year = int((request.form.get('year') or str(anchor.year)).strip())
        quincena = (request.form.get('quincena') or '1').strip()
    if quincena not in ('1', '2') or month < 1 or month > 12:
        flash('Invalid period.', 'error')
        return redirect(url_for('routes.dtr_regenerate'))
    try:
        pair_n = _run_dtr_quincena_regeneration(year, month, quincena, getattr(current_user, 'id', None))
        period = dtr_period.get_or_create_period(year, month, quincena)
        dtr_period.mark_processed(period, getattr(current_user, 'id', None))
        dtr_period.notify_employees_worktime_processed(year, month, quincena)
        dtr_period.notify_hr_worktime_processed(year, month, quincena)
        db.session.commit()
        flash('Worktime processing completed.', 'success')
        if pair_n:
            flash(
                f'Overnight punch pairing moved {pair_n} early-morning Out '
                f'time(s) onto flexi start days before computing worktime.',
                'success',
            )
    except Exception as e:
        db.session.rollback()
        flash(f'Worktime processing failed: {e}', 'error')
        return redirect(url_for('routes.dtr_regenerate'))
    return redirect(url_for('routes.dtr_regenerate_summary', year=year, month=month, quincena=quincena))


@bp.route('/dtr/overnight-punch-fix', methods=['GET', 'POST'])
@login_required
def dtr_overnight_punch_fix():
    """Admin/HR/Payroll Maker: preview or apply overnight Out auto-move onto flexi start days."""
    denied = _require_flexible_worktime_manager()
    if denied:
        return denied

    from app.dtr_overnight_punch_service import pair_overnight_punches_for_quincena

    anchor = date.today()
    opts = _build_quincena_upload_options(anchor)
    default_quincena = f'{anchor.year:04d}-{anchor.month:02d}-{"1" if anchor.day <= 15 else "2"}'
    raw_period = (request.values.get('quincena_period') or default_quincena).strip()
    year = month = None
    quincena = '1'
    py, pm, pq = _parse_quincena_upload_value(raw_period)
    if py is not None:
        year, month, quincena = py, pm, pq
    else:
        year, month, quincena = anchor.year, anchor.month, ('1' if anchor.day <= 15 else '2')
        raw_period = f'{year:04d}-{month:02d}-{quincena}'

    start_d, end_d = _quincena_date_range(year, month, quincena)
    period_label = (
        f'{date(year, month, 1).strftime("%B %Y")} — '
        f'{"1st" if quincena == "1" else "2nd"} quincena'
    )
    maker_dept_id = _payroll_maker_department_id()
    is_payroll_maker_scope = (
        (getattr(current_user, 'role', None) or '').strip().lower() == 'payroll_maker'
        and maker_dept_id is not None
    )
    emp_q = Employee.query.filter_by(status='active')
    if maker_dept_id is not None:
        emp_q = emp_q.filter(Employee.department_id == maker_dept_id)
    employees = emp_q.order_by(Employee.last_name, Employee.first_name).all()
    moves = []
    applied = 0
    previewed = False

    if request.method == 'POST':
        action = (request.form.get('action') or 'preview').strip().lower()
        dry_run = action != 'apply'
        try:
            moves, applied = pair_overnight_punches_for_quincena(
                year=year,
                month=month,
                quincena_half=quincena,
                start_d=start_d,
                end_d=end_d,
                employees=employees,
                duty_schedule_from_row=_duty_schedule_from_flexible_worktime_row,
                entry_date_bounds=_flexible_worktime_entry_date_bounds,
                employee_on_flexi=_employee_on_flexible_worktime_quincena,
                dry_run=dry_run,
            )
            previewed = True
            if dry_run:
                flash(
                    f'Preview: {len(moves)} overnight Out move(s) would be applied for {period_label}.',
                    'success' if moves else 'info',
                )
            else:
                db.session.commit()
                if is_payroll_maker_scope:
                    flash(
                        f'Applied {applied} overnight Out move(s) for {period_label}. '
                        f'Ask Admin/HR to re-run Process Worktime so late/undertime snapshots refresh.',
                        'success' if applied else 'info',
                    )
                else:
                    flash(
                        f'Applied {applied} overnight Out move(s) for {period_label}. '
                        f'Re-run Process Worktime so late/undertime snapshots refresh.',
                        'success' if applied else 'info',
                    )
                # Re-preview after apply (should be empty if all applied)
                moves, _ = pair_overnight_punches_for_quincena(
                    year=year,
                    month=month,
                    quincena_half=quincena,
                    start_d=start_d,
                    end_d=end_d,
                    employees=employees,
                    duty_schedule_from_row=_duty_schedule_from_flexible_worktime_row,
                    entry_date_bounds=_flexible_worktime_entry_date_bounds,
                    employee_on_flexi=_employee_on_flexible_worktime_quincena,
                    dry_run=True,
                )
        except Exception as e:
            db.session.rollback()
            flash(f'Overnight punch fix failed: {e}', 'error')

    employee = current_user.employee if current_user.employee else None
    return render_template(
        'dtr_overnight_punch_fix.html',
        employee=employee,
        quincena_options=opts,
        default_quincena=raw_period,
        period_label=period_label,
        year=year,
        month=month,
        quincena=quincena,
        moves=moves,
        previewed=previewed,
        applied_count=applied,
        is_payroll_maker_scope=is_payroll_maker_scope,
    )


@bp.route('/dtr/regenerate/summary')
@login_required
def dtr_regenerate_summary():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    month = int((request.args.get('month') or str(date.today().month)).strip())
    year = int((request.args.get('year') or str(date.today().year)).strip())
    quincena = (request.args.get('quincena') or '1').strip()
    if quincena not in ('1', '2'):
        flash('Invalid quincena.', 'error')
        return redirect(url_for('routes.dtr_regenerate'))
    period_label = f'{date(year, month, 1).strftime("%B %Y")} — {"1st" if quincena == "1" else "2nd"} quincena'
    dept_rows = _dtr_regen_summary_department_totals(year, month, quincena)
    emp_rows = _dtr_regen_summary_employee_rows(year, month, quincena)
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'dtr_regenerate_summary.html',
        year=year,
        month=month,
        quincena=quincena,
        period_label=period_label,
        dept_rows=dept_rows,
        emp_rows=emp_rows,
        employee=employee,
    )


@bp.route('/dtr/regenerate/summary/export')
@login_required
def dtr_regenerate_summary_export():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    month = int((request.args.get('month') or str(date.today().month)).strip())
    year = int((request.args.get('year') or str(date.today().year)).strip())
    quincena = (request.args.get('quincena') or '1').strip()
    fmt = (request.args.get('format') or 'csv').strip().lower()
    if quincena not in ('1', '2'):
        flash('Invalid quincena.', 'error')
        return redirect(url_for('routes.dtr_regenerate'))
    period_label = f'{date(year, month, 1).strftime("%B %Y")} — {"1st" if quincena == "1" else "2nd"} quincena'
    dept_rows = _dtr_regen_summary_department_totals(year, month, quincena)
    emp_rows = _dtr_regen_summary_employee_rows(year, month, quincena)
    base = f'dtr_worktime_summary_{year}_{month:02d}_Q{quincena}'
    if fmt == 'csv':
        data = _dtr_regen_summary_csv_bytes(dept_rows, emp_rows, period_label)
        return Response(
            data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={base}.csv'},
        )
    if fmt == 'pdf':
        buf = _dtr_regen_summary_pdf_bytes(dept_rows, emp_rows, period_label)
        return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{base}.pdf')
    flash('Invalid export format. Use csv or pdf.', 'error')
    return redirect(url_for('routes.dtr_regenerate_summary', year=year, month=month, quincena=quincena))


@bp.route('/payroll/deductions/gsis')
@login_required
def payroll_deductions_gsis():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    today = date.today()
    year_raw = (request.args.get('year') or str(today.year)).strip()
    month_raw = (request.args.get('month') or str(today.month)).strip()
    dept_raw = (request.args.get('department_id') or '').strip()
    do_generate = (request.args.get('generate') or '').strip() == '1'

    try:
        year = int(year_raw)
    except ValueError:
        year = today.year
    try:
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        month = today.month

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    selected_department = dept_by_id.get(department_id) if department_id else None

    rows = []
    if do_generate:
        q = Employee.query.options(db.joinedload(Employee.department)).filter_by(status='active')
        if selected_department:
            q = q.filter(Employee.department_id == selected_department.id)
        employees = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()
        for emp in employees:
            if _is_jo_cos_employee(emp):
                continue
            rows.append(_gsis_contribution_row_dict(emp))

    loan_year_raw = (request.args.get('loan_year') or year_raw).strip()
    loan_month_raw = (request.args.get('loan_month') or month_raw).strip()
    loan_dept_raw = (request.args.get('loan_department_id') or dept_raw or '').strip()
    loan_load = (request.args.get('loan_load') or '').strip() == '1'
    loan_view = (request.args.get('loan_view') or '').strip().lower()
    try:
        loan_year = int(loan_year_raw)
    except ValueError:
        loan_year = year
    try:
        loan_month = int(loan_month_raw)
        if loan_month < 1 or loan_month > 12:
            raise ValueError()
    except ValueError:
        loan_month = month
    loan_department_id = None
    if loan_dept_raw:
        try:
            loan_department_id = int(loan_dept_raw)
        except ValueError:
            loan_department_id = None
    loan_selected_department = dept_by_id.get(loan_department_id) if loan_department_id else None

    has_loan_data = GsisLoanRecord.query.filter_by(year=loan_year, month=loan_month).first() is not None
    show_loan_upload = loan_view == 'upload'

    loan_rows = []
    if loan_load and has_loan_data:
        loan_rows = _gsis_loan_load_rows(loan_year, loan_month, loan_department_id)

    return render_template(
        'payroll/gsis.html',
        employee=employee,
        departments=departments,
        selected_department=selected_department,
        year=year,
        month=month,
        rows=rows,
        generated=do_generate,
        loan_year=loan_year,
        loan_month=loan_month,
        loan_selected_department=loan_selected_department,
        loan_rows=loan_rows,
        loan_loaded=loan_load and has_loan_data,
        show_loan_upload=show_loan_upload,
        has_loan_data=has_loan_data,
        gsis_loan_types=GSIS_LOAN_TYPE_FIELDS,
        active_tab=_gsis_active_tab(),
    )


@bp.route('/payroll/deductions/gsis/submit', methods=['POST'])
@login_required
def payroll_deductions_gsis_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    today = date.today()
    year_raw = (request.form.get('year') or str(today.year)).strip()
    month_raw = (request.form.get('month') or str(today.month)).strip()
    dept_raw = (request.form.get('department_id') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()

    try:
        year = int(year_raw)
    except ValueError:
        flash('Invalid year.', 'error')
        return redirect(url_for('routes.payroll_deductions_gsis'))
    try:
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid month.', 'error')
        return redirect(url_for('routes.payroll_deductions_gsis', year=year))

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    if not payload:
        flash('No rows to submit.', 'warning')
        return redirect(url_for('routes.payroll_deductions_gsis', year=year, month=month, department_id=department_id, generate=1))

    emp_ids = []
    row_map = {}
    for r in payload:
        try:
            emp_id = int(r.get('employee_pk'))
        except Exception:
            continue
        emp_ids.append(emp_id)
        row_map[emp_id] = {
            'ps_amount': _money_decimal(r.get('ps_amount')),
            'gs_amount': _money_decimal(r.get('gs_amount')),
            'month_amount': _money_decimal(r.get('month_amount')),
        }

    if not emp_ids:
        flash('No valid employees to submit.', 'error')
        return redirect(url_for('routes.payroll_deductions_gsis', year=year, month=month, department_id=department_id, generate=1))

    employees = (Employee.query.options(db.joinedload(Employee.department))
                 .filter(Employee.id.in_(emp_ids))
                 .filter_by(status='active')
                 .all())
    emp_by_id = {e.id: e for e in employees}
    quincena = GSIS_CONTRIBUTION_DEDUCTIBLE_QUINCENA

    try:
        for emp_id in emp_ids:
            (GsisContribution.query
             .filter_by(employee_id=emp_id, year=year, month=month, deductible_quincena=quincena)
             .delete(synchronize_session=False))

        inserted = 0
        for emp_id in emp_ids:
            emp = emp_by_id.get(emp_id)
            if not emp or _is_jo_cos_employee(emp):
                continue
            row_data = row_map.get(emp_id, {})
            basic = _current_salary_amount(emp) or 0.0
            amounts = _gsis_contribution_amounts(basic)
            ps = _money_decimal(row_data.get('ps_amount', amounts['ps_amount']))
            gs = _money_decimal(row_data.get('gs_amount', amounts['gs_amount']))
            month_amount = ps

            db.session.add(GsisContribution(
                employee_id=emp.id,
                department_id=emp.department_id,
                year=year,
                month=month,
                deductible_quincena=quincena,
                basic_salary=amounts['basic_salary'],
                ps_amount=ps,
                gs_amount=gs,
                month_amount=month_amount,
                quincena_amount=Decimal('0.00'),
                total_amount=month_amount,
                deducted_amount=month_amount,
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1

        db.session.commit()
        flash(f'GSIS contributions saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving GSIS contributions: {str(e)}', 'error')

    return redirect(url_for('routes.payroll_deductions_gsis', year=year, month=month, department_id=department_id, generate=1))


@bp.route('/payroll/deductions/phic')
@login_required
def payroll_deductions_phic():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    today = date.today()
    year_raw = (request.args.get('year') or str(today.year)).strip()
    month_raw = (request.args.get('month') or str(today.month)).strip()
    dept_raw = (request.args.get('department_id') or '').strip()
    do_generate = (request.args.get('generate') or '').strip() == '1'

    try:
        year = int(year_raw)
    except ValueError:
        year = today.year
    try:
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        month = today.month

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    selected_department = dept_by_id.get(department_id) if department_id else None

    rows = []
    if do_generate:
        q = Employee.query.options(db.joinedload(Employee.department)).filter_by(status='active')
        if selected_department:
            q = q.filter(Employee.department_id == selected_department.id)
        employees = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()
        for emp in employees:
            if _is_jo_cos_employee(emp):
                continue
            rows.append(_phic_contribution_row_dict(emp))

    return render_template(
        'payroll/phic.html',
        employee=employee,
        departments=departments,
        selected_department=selected_department,
        year=year,
        month=month,
        rows=rows,
        generated=do_generate,
    )


@bp.route('/payroll/deductions/phic/submit', methods=['POST'])
@login_required
def payroll_deductions_phic_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    today = date.today()
    year_raw = (request.form.get('year') or str(today.year)).strip()
    month_raw = (request.form.get('month') or str(today.month)).strip()
    dept_raw = (request.form.get('department_id') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()

    try:
        year = int(year_raw)
    except ValueError:
        flash('Invalid year.', 'error')
        return redirect(url_for('routes.payroll_deductions_phic'))
    try:
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid month.', 'error')
        return redirect(url_for('routes.payroll_deductions_phic', year=year))

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    if not payload:
        flash('No rows to submit.', 'warning')
        return redirect(url_for('routes.payroll_deductions_phic', year=year, month=month, department_id=department_id, generate=1))

    emp_ids = []
    row_map = {}
    for r in payload:
        try:
            emp_id = int(r.get('employee_pk'))
        except Exception:
            continue
        emp_ids.append(emp_id)
        row_map[emp_id] = {
            'ps_amount': _money_decimal(r.get('ps_amount')),
            'gs_amount': _money_decimal(r.get('gs_amount')),
            'month_amount': _money_decimal(r.get('month_amount')),
        }

    if not emp_ids:
        flash('No valid employees to submit.', 'error')
        return redirect(url_for('routes.payroll_deductions_phic', year=year, month=month, department_id=department_id, generate=1))

    employees = (Employee.query.options(db.joinedload(Employee.department))
                 .filter(Employee.id.in_(emp_ids))
                 .filter_by(status='active')
                 .all())
    emp_by_id = {e.id: e for e in employees}
    quincena = PHIC_CONTRIBUTION_DEDUCTIBLE_QUINCENA

    try:
        for emp_id in emp_ids:
            (PhicContribution.query
             .filter_by(employee_id=emp_id, year=year, month=month, deductible_quincena=quincena)
             .delete(synchronize_session=False))

        inserted = 0
        for emp_id in emp_ids:
            emp = emp_by_id.get(emp_id)
            if not emp or _is_jo_cos_employee(emp):
                continue
            row_data = row_map.get(emp_id, {})
            basic = _current_salary_amount(emp) or 0.0
            amounts = _phic_contribution_amounts(basic)
            ps = _money_decimal(row_data.get('ps_amount', amounts['ps_amount']))
            gs = _money_decimal(row_data.get('gs_amount', amounts['gs_amount']))
            month_amount = ps

            db.session.add(PhicContribution(
                employee_id=emp.id,
                department_id=emp.department_id,
                year=year,
                month=month,
                deductible_quincena=quincena,
                basic_salary=amounts['basic_salary'],
                ps_amount=ps,
                gs_amount=gs,
                month_amount=month_amount,
                quincena_amount=Decimal('0.00'),
                total_amount=month_amount,
                deducted_amount=month_amount,
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1

        db.session.commit()
        flash(f'PHIC contributions saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving PHIC contributions: {str(e)}', 'error')

    return redirect(url_for('routes.payroll_deductions_phic', year=year, month=month, department_id=department_id, generate=1))


@bp.route('/payroll/deductions/lbp')
@login_required
def payroll_deductions_lbp():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    today = date.today()
    year_raw = (request.args.get('year') or str(today.year)).strip()
    month_raw = (request.args.get('month') or str(today.month)).strip()
    dept_raw = (request.args.get('department_id') or '').strip()
    loan_view = (request.args.get('loan_view') or '').strip().lower()
    loan_load = (request.args.get('loan_load') or '').strip() == '1'

    try:
        year = int(year_raw)
    except ValueError:
        year = today.year
    try:
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        month = today.month

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    selected_department = dept_by_id.get(department_id) if department_id else None

    has_billing_data = LbpBillingRecord.query.filter_by(year=year, month=month).first() is not None
    show_upload = loan_view == 'upload'

    billing_rows = []
    if loan_load and has_billing_data:
        billing_rows = _lbp_billing_load_rows(year, month, department_id)

    return render_template(
        'payroll/lbp.html',
        employee=employee,
        departments=departments,
        selected_department=selected_department,
        year=year,
        month=month,
        billing_rows=billing_rows,
        billing_loaded=loan_load and has_billing_data,
        show_upload=show_upload,
        has_billing_data=has_billing_data,
    )


@bp.route('/payroll/deductions/lbp/preview', methods=['POST'])
@login_required
def payroll_deductions_lbp_preview():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    upload = request.files.get('lbp_billing_file')
    if not upload or not upload.filename:
        return jsonify({'ok': False, 'error': 'Select an LBP billing file to upload.'}), 400
    if not upload.filename.lower().endswith(('.xls', '.xlsx')):
        return jsonify({'ok': False, 'error': 'Upload an Excel file (.xls or .xlsx).'}), 400
    try:
        data = _parse_lbp_billing_xls(upload.read())
        return jsonify({'ok': True, **data})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@bp.route('/payroll/deductions/lbp/upload-submit', methods=['POST'])
@login_required
def payroll_deductions_lbp_upload_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month for LBP billing upload.', 'error')
        return redirect(url_for('routes.payroll_deductions_lbp', loan_view='upload'))

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    matched = [r for r in payload if r.get('employee_pk')]
    if not matched:
        flash('No matched employees to save from the upload.', 'warning')
        return redirect(url_for('routes.payroll_deductions_lbp', loan_view='upload'))

    try:
        LbpBillingRecord.query.filter_by(year=year, month=month).delete(synchronize_session=False)
        inserted = 0
        for r in matched:
            emp_id = int(r['employee_pk'])
            db.session.add(LbpBillingRecord(
                employee_id=emp_id,
                year=year,
                month=month,
                employee_no=(r.get('employee_no') or '').strip() or None,
                percov=(r.get('percov') or '').strip() or None,
                q1_amount=_money_decimal(r.get('q1_amount')),
                q2_amount=_money_decimal(r.get('q2_amount')),
                total_payable=_money_decimal(r.get('total_payable')),
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'LBP billing file saved ({inserted} employees).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving LBP billing upload: {str(e)}', 'error')
        return redirect(url_for('routes.payroll_deductions_lbp', loan_view='upload'))

    return redirect(url_for(
        'routes.payroll_deductions_lbp',
        year=year,
        month=month,
        loan_view='load',
        loan_load=1,
    ))


@bp.route('/payroll/deductions/lbp/deductions-submit', methods=['POST'])
@login_required
def payroll_deductions_lbp_deductions_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    dept_raw = (request.form.get('department_id') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month.', 'error')
        return redirect(url_for('routes.payroll_deductions_lbp', loan_view='load'))

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    if not payload:
        flash('No billing deduction rows to save.', 'warning')
        return redirect(url_for(
            'routes.payroll_deductions_lbp',
            year=year, month=month, department_id=department_id or '',
            loan_load=1, loan_view='load',
        ))

    try:
        employee_ids = {int(r.get('employee_pk')) for r in payload if r.get('employee_pk')}
        for emp_id in employee_ids:
            (LbpBillingDeduction.query
             .filter_by(employee_id=emp_id, year=year, month=month)
             .delete(synchronize_session=False))

        inserted = 0
        for r in payload:
            emp_id = int(r.get('employee_pk'))
            month_amount = _money_decimal(r.get('month_amount'))
            q1_amount = _money_decimal(r.get('q1_amount'))
            q2_amount = _money_decimal(r.get('q2_amount'))
            q1_on = str(r.get('q1_enabled', '1')).lower() in ('1', 'true', 'yes', 'on')
            q2_on = str(r.get('q2_enabled', '1')).lower() in ('1', 'true', 'yes', 'on')
            emp = Employee.query.get(emp_id)
            if not emp:
                continue
            db.session.add(LbpBillingDeduction(
                employee_id=emp_id,
                department_id=emp.department_id,
                year=year,
                month=month,
                month_amount=month_amount,
                q1_amount=q1_amount,
                q2_amount=q2_amount,
                q1_enabled=q1_on,
                q2_enabled=q2_on,
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'LBP billing deductions saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving LBP billing deductions: {str(e)}', 'error')

    return redirect(url_for(
        'routes.payroll_deductions_lbp',
        year=year, month=month, department_id=department_id or '',
        loan_load=1, loan_view='load',
    ))


@bp.route('/payroll/deductions/jofemco')
@login_required
def payroll_deductions_jofemco():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    today = date.today()
    year_raw = (request.args.get('year') or str(today.year)).strip()
    month_raw = (request.args.get('month') or str(today.month)).strip()
    dept_raw = (request.args.get('department_id') or '').strip()
    loan_view = (request.args.get('loan_view') or '').strip().lower()
    loan_load = (request.args.get('loan_load') or '').strip() == '1'

    try:
        year = int(year_raw)
    except ValueError:
        year = today.year
    try:
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        month = today.month

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    selected_department = dept_by_id.get(department_id) if department_id else None

    has_billing_data = JofemcoBillingRecord.query.filter_by(year=year, month=month).first() is not None
    show_upload = loan_view == 'upload'

    billing_rows = []
    if loan_load and has_billing_data:
        billing_rows = _jofemco_billing_load_rows(year, month, department_id)

    return render_template(
        'payroll/jofemco.html',
        employee=employee,
        departments=departments,
        selected_department=selected_department,
        year=year,
        month=month,
        billing_rows=billing_rows,
        billing_loaded=loan_load and has_billing_data,
        show_upload=show_upload,
        has_billing_data=has_billing_data,
    )


@bp.route('/payroll/deductions/jofemco/preview', methods=['POST'])
@login_required
def payroll_deductions_jofemco_preview():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    upload = request.files.get('jofemco_billing_file')
    if not upload or not upload.filename:
        return jsonify({'ok': False, 'error': 'Select a JOFEMCO billing file to upload.'}), 400
    if not upload.filename.lower().endswith(('.xls', '.xlsx')):
        return jsonify({'ok': False, 'error': 'Upload an Excel file (.xls or .xlsx).'}), 400
    try:
        data = _parse_jofemco_billing_xls(upload.read())
        return jsonify({'ok': True, **data})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@bp.route('/payroll/deductions/jofemco/upload-submit', methods=['POST'])
@login_required
def payroll_deductions_jofemco_upload_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month for JOFEMCO billing upload.', 'error')
        return redirect(url_for('routes.payroll_deductions_jofemco', loan_view='upload'))

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    matched = [r for r in payload if r.get('employee_pk')]
    if not matched:
        flash('No matched employees to save from the upload.', 'warning')
        return redirect(url_for('routes.payroll_deductions_jofemco', loan_view='upload'))

    try:
        JofemcoBillingRecord.query.filter_by(year=year, month=month).delete(synchronize_session=False)
        inserted = 0
        for r in matched:
            emp_id = int(r['employee_pk'])
            db.session.add(JofemcoBillingRecord(
                employee_id=emp_id,
                year=year,
                month=month,
                employee_no=(r.get('employee_no') or '').strip() or None,
                percov=(r.get('percov') or '').strip() or None,
                loan_q1=_money_decimal(r.get('loan_q1')),
                loan_q2=_money_decimal(r.get('loan_q2')),
                canteen_q1=_money_decimal(r.get('canteen_q1')),
                canteen_q2=_money_decimal(r.get('canteen_q2')),
                total_payable=_money_decimal(r.get('total_payable')),
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'JOFEMCO billing file saved ({inserted} employees).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving JOFEMCO billing upload: {str(e)}', 'error')
        return redirect(url_for('routes.payroll_deductions_jofemco', loan_view='upload'))

    return redirect(url_for(
        'routes.payroll_deductions_jofemco',
        year=year,
        month=month,
        loan_view='load',
        loan_load=1,
    ))


@bp.route('/payroll/deductions/jofemco/deductions-submit', methods=['POST'])
@login_required
def payroll_deductions_jofemco_deductions_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    dept_raw = (request.form.get('department_id') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month.', 'error')
        return redirect(url_for('routes.payroll_deductions_jofemco', loan_view='load'))

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    if not payload:
        flash('No billing deduction rows to save.', 'warning')
        return redirect(url_for(
            'routes.payroll_deductions_jofemco',
            year=year, month=month, department_id=department_id or '',
            loan_load=1, loan_view='load',
        ))

    try:
        employee_ids = {int(r.get('employee_pk')) for r in payload if r.get('employee_pk')}
        for emp_id in employee_ids:
            (JofemcoBillingDeduction.query
             .filter_by(employee_id=emp_id, year=year, month=month)
             .delete(synchronize_session=False))

        inserted = 0
        for r in payload:
            emp_id = int(r.get('employee_pk'))
            month_amount = _money_decimal(r.get('month_amount'))
            q1_amount = _money_decimal(r.get('q1_amount'))
            q2_amount = _money_decimal(r.get('q2_amount'))
            q1_on = str(r.get('q1_enabled', '1')).lower() in ('1', 'true', 'yes', 'on')
            q2_on = str(r.get('q2_enabled', '1')).lower() in ('1', 'true', 'yes', 'on')
            emp = Employee.query.get(emp_id)
            if not emp:
                continue
            db.session.add(JofemcoBillingDeduction(
                employee_id=emp_id,
                department_id=emp.department_id,
                year=year,
                month=month,
                month_amount=month_amount,
                q1_amount=q1_amount,
                q2_amount=q2_amount,
                q1_enabled=q1_on,
                q2_enabled=q2_on,
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'JOFEMCO billing deductions saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving JOFEMCO billing deductions: {str(e)}', 'error')

    return redirect(url_for(
        'routes.payroll_deductions_jofemco',
        year=year, month=month, department_id=department_id or '',
        loan_load=1, loan_view='load',
    ))


@bp.route('/payroll/deductions/withholding-tax')
@login_required
def payroll_deductions_withholding_tax():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    today = date.today()
    year_raw = (request.args.get('year') or str(today.year)).strip()
    month_raw = (request.args.get('month') or str(today.month)).strip()
    dept_raw = (request.args.get('department_id') or '').strip()
    view = (request.args.get('loan_view') or '').strip().lower()  # reuse param name from cloned pages
    do_load = (request.args.get('loan_load') or '').strip() == '1'

    try:
        year = int(year_raw)
    except ValueError:
        year = today.year
    try:
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        month = today.month

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    selected_department = dept_by_id.get(department_id) if department_id else None

    has_data = WithholdingTaxRecord.query.filter_by(year=year, month=month).first() is not None
    show_upload = view == 'upload'

    rows = []
    if do_load and has_data:
        rows = _withholding_tax_load_rows(year, month, department_id)

    return render_template(
        'payroll/withholding_tax.html',
        employee=employee,
        departments=departments,
        selected_department=selected_department,
        year=year,
        month=month,
        rows=rows,
        loaded=do_load and has_data,
        show_upload=show_upload,
        has_data=has_data,
    )


@bp.route('/payroll/deductions/withholding-tax/preview', methods=['POST'])
@login_required
def payroll_deductions_withholding_tax_preview():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    upload = request.files.get('withholding_tax_file')
    if not upload or not upload.filename:
        return jsonify({'ok': False, 'error': 'Select a withholding tax file to upload.'}), 400
    if not upload.filename.lower().endswith(('.xls', '.xlsx')):
        return jsonify({'ok': False, 'error': 'Upload an Excel file (.xls or .xlsx).'}), 400
    try:
        data = _parse_withholding_tax_xlsx(upload.read())
        return jsonify({'ok': True, **data})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@bp.route('/payroll/deductions/withholding-tax/upload-submit', methods=['POST'])
@login_required
def payroll_deductions_withholding_tax_upload_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month for withholding tax upload.', 'error')
        return redirect(url_for('routes.payroll_deductions_withholding_tax', loan_view='upload'))

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    matched = [r for r in payload if r.get('employee_pk')]
    if not matched:
        flash('No matched employees to save from the upload.', 'warning')
        return redirect(url_for('routes.payroll_deductions_withholding_tax', loan_view='upload'))

    try:
        WithholdingTaxRecord.query.filter_by(year=year, month=month).delete(synchronize_session=False)
        inserted = 0
        for r in matched:
            emp_id = int(r['employee_pk'])
            db.session.add(WithholdingTaxRecord(
                employee_id=emp_id,
                year=year,
                month=month,
                employee_no=(r.get('employee_no') or '').strip() or None,
                percov=(r.get('percov') or '').strip() or None,
                q1_amount=_money_decimal(r.get('q1_amount')),
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'Withholding tax file saved ({inserted} employees).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving withholding tax upload: {str(e)}', 'error')
        return redirect(url_for('routes.payroll_deductions_withholding_tax', loan_view='upload'))

    return redirect(url_for(
        'routes.payroll_deductions_withholding_tax',
        year=year,
        month=month,
        loan_view='load',
        loan_load=1,
    ))


@bp.route('/payroll/deductions/withholding-tax/deductions-submit', methods=['POST'])
@login_required
def payroll_deductions_withholding_tax_deductions_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    dept_raw = (request.form.get('department_id') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month.', 'error')
        return redirect(url_for('routes.payroll_deductions_withholding_tax', loan_view='load'))

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    if not payload:
        flash('No withholding tax rows to save.', 'warning')
        return redirect(url_for(
            'routes.payroll_deductions_withholding_tax',
            year=year, month=month, department_id=department_id or '',
            loan_load=1, loan_view='load',
        ))

    try:
        employee_ids = {int(r.get('employee_pk')) for r in payload if r.get('employee_pk')}
        for emp_id in employee_ids:
            (WithholdingTaxDeduction.query
             .filter_by(employee_id=emp_id, year=year, month=month)
             .delete(synchronize_session=False))

        inserted = 0
        for r in payload:
            emp_id = int(r.get('employee_pk'))
            q1_amount = _money_decimal(r.get('q1_amount'))
            month_amount = q1_amount
            emp = Employee.query.get(emp_id)
            if not emp:
                continue
            db.session.add(WithholdingTaxDeduction(
                employee_id=emp_id,
                department_id=emp.department_id,
                year=year,
                month=month,
                month_amount=month_amount,
                q1_amount=q1_amount,
                q2_amount=Decimal('0.00'),
                q1_enabled=True,
                q2_enabled=False,
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'Withholding tax deductions saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving withholding tax deductions: {str(e)}', 'error')

    return redirect(url_for(
        'routes.payroll_deductions_withholding_tax',
        year=year, month=month, department_id=department_id or '',
        loan_load=1, loan_view='load',
    ))


@bp.route('/payroll/deductions/gsis/loans/preview', methods=['POST'])
@login_required
def payroll_deductions_gsis_loans_preview():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    upload = request.files.get('gsis_loan_file')
    if not upload or not upload.filename:
        return jsonify({'ok': False, 'error': 'Select a GSIS loan file to upload.'}), 400
    if not upload.filename.lower().endswith(('.xls', '.xlsx')):
        return jsonify({'ok': False, 'error': 'Upload an Excel file (.xls or .xlsx).'}), 400
    try:
        data = _parse_gsis_loan_xls(upload.read())
        return jsonify({'ok': True, **data})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@bp.route('/payroll/deductions/gsis/loans/upload-submit', methods=['POST'])
@login_required
def payroll_deductions_gsis_loans_upload_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month for GSIS loan upload.', 'error')
        return redirect(url_for('routes.payroll_deductions_gsis', loan_view='upload') + '#loan')

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    matched = [r for r in payload if r.get('employee_pk')]
    if not matched:
        flash('No matched employees to save from the upload.', 'warning')
        return redirect(url_for('routes.payroll_deductions_gsis', loan_view='upload') + '#loan')

    try:
        GsisLoanRecord.query.filter_by(year=year, month=month).delete(synchronize_session=False)
        inserted = 0
        for r in matched:
            emp_id = int(r['employee_pk'])
            db.session.add(GsisLoanRecord(
                employee_id=emp_id,
                year=year,
                month=month,
                bpno=(r.get('bpno') or '').strip() or None,
                ps_amount=_money_decimal(r.get('ps_amount')),
                gs_amount=_money_decimal(r.get('gs_amount')),
                ec_amount=_money_decimal(r.get('ec_amount')),
                consoloan=_money_decimal(r.get('consoloan')),
                emrgyln=_money_decimal(r.get('emrgyln')),
                plreg=_money_decimal(r.get('plreg')),
                gfal=_money_decimal(r.get('gfal')),
                mpl=_money_decimal(r.get('mpl')),
                cpl=_money_decimal(r.get('cpl')),
                mpl_lite=_money_decimal(r.get('mpl_lite')),
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'GSIS loan file saved ({inserted} employees).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving GSIS loan upload: {str(e)}', 'error')
        return redirect(url_for('routes.payroll_deductions_gsis', loan_view='upload') + '#loan')

    return redirect(url_for(
        'routes.payroll_deductions_gsis',
        loan_year=year,
        loan_month=month,
        loan_view='load',
        loan_uploaded=1,
    ) + '#loan')


@bp.route('/payroll/deductions/gsis/loans/deductions-submit', methods=['POST'])
@login_required
def payroll_deductions_gsis_loans_deductions_submit():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    dept_raw = (request.form.get('department_id') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month.', 'error')
        return redirect(url_for('routes.payroll_deductions_gsis', loan_view='load') + '#loan')

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []

    if not payload:
        flash('No loan deduction rows to save.', 'warning')
        return redirect(url_for(
            'routes.payroll_deductions_gsis',
            loan_year=year, loan_month=month, loan_department_id=department_id or '',
            loan_load=1, loan_view='load',
        ) + '#loan')

    try:
        employee_ids = set()
        for r in payload:
            employee_ids.add(int(r.get('employee_pk')))

        for emp_id in employee_ids:
            (GsisLoanDeduction.query
             .filter_by(employee_id=emp_id, year=year, month=month)
             .delete(synchronize_session=False))

        inserted = 0
        for r in payload:
            emp_id = int(r.get('employee_pk'))
            loan_type = (r.get('loan_type') or '').strip()
            if not loan_type:
                continue
            month_amount = _money_decimal(r.get('month_amount'))
            q1_amount = _money_decimal(r.get('q1_amount'))
            q2_amount = _money_decimal(r.get('q2_amount'))
            q1_on = str(r.get('q1_enabled', '1')).lower() in ('1', 'true', 'yes', 'on')
            q2_on = str(r.get('q2_enabled', '1')).lower() in ('1', 'true', 'yes', 'on')
            emp = Employee.query.get(emp_id)
            if not emp:
                continue
            db.session.add(GsisLoanDeduction(
                employee_id=emp_id,
                department_id=emp.department_id,
                year=year,
                month=month,
                loan_type=loan_type,
                month_amount=month_amount,
                q1_amount=q1_amount,
                q2_amount=q2_amount,
                q1_enabled=q1_on,
                q2_enabled=q2_on,
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'GSIS loan deductions saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving GSIS loan deductions: {str(e)}', 'error')

    return redirect(url_for(
        'routes.payroll_deductions_gsis',
        loan_year=year, loan_month=month, loan_department_id=department_id or '',
        loan_load=1, loan_view='load',
    ) + '#loan')


def _render_hdmf_page(url_scope: str):
    employment_scope = _hdmf_employment_scope(url_scope)
    if not employment_scope:
        abort(404)
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    today = date.today()
    year_raw = (request.args.get('year') or str(today.year)).strip()
    month_raw = (request.args.get('month') or str(today.month)).strip()
    dept_raw = (request.args.get('department_id') or '').strip()

    try:
        year = int(year_raw)
    except ValueError:
        year = today.year
    try:
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        month = today.month

    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None

    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    selected_department = dept_by_id.get(department_id) if department_id else None

    contrib_year_raw = (request.args.get('contrib_year') or year_raw).strip()
    contrib_month_raw = (request.args.get('contrib_month') or month_raw).strip()
    contrib_dept_raw = (request.args.get('contrib_department_id') or dept_raw or '').strip()
    contrib_load = (request.args.get('contrib_load') or '').strip() == '1'
    contrib_view = (request.args.get('contrib_view') or '').strip().lower()
    try:
        contrib_year = int(contrib_year_raw)
    except ValueError:
        contrib_year = year
    try:
        contrib_month = int(contrib_month_raw)
        if contrib_month < 1 or contrib_month > 12:
            raise ValueError()
    except ValueError:
        contrib_month = month
    contrib_department_id = None
    if contrib_dept_raw:
        try:
            contrib_department_id = int(contrib_dept_raw)
        except ValueError:
            contrib_department_id = None
    contrib_selected_department = dept_by_id.get(contrib_department_id) if contrib_department_id else None

    loan_year_raw = (request.args.get('loan_year') or year_raw).strip()
    loan_month_raw = (request.args.get('loan_month') or month_raw).strip()
    loan_dept_raw = (request.args.get('loan_department_id') or dept_raw or '').strip()
    loan_load = (request.args.get('loan_load') or '').strip() == '1'
    loan_view = (request.args.get('loan_view') or '').strip().lower()
    try:
        loan_year = int(loan_year_raw)
    except ValueError:
        loan_year = year
    try:
        loan_month = int(loan_month_raw)
        if loan_month < 1 or loan_month > 12:
            raise ValueError()
    except ValueError:
        loan_month = month
    loan_department_id = None
    if loan_dept_raw:
        try:
            loan_department_id = int(loan_dept_raw)
        except ValueError:
            loan_department_id = None
    loan_selected_department = dept_by_id.get(loan_department_id) if loan_department_id else None

    has_contrib_data = HdmfContributionRecord.query.filter_by(
        year=contrib_year, month=contrib_month, employment_scope=employment_scope,
    ).first() is not None
    has_loan_data = HdmfLoanRecord.query.filter_by(
        year=loan_year, month=loan_month, employment_scope=employment_scope,
    ).first() is not None
    show_contrib_upload = contrib_view == 'upload'
    show_loan_upload = loan_view == 'upload'

    contrib_rows = []
    if contrib_load:
        contrib_rows = _hdmf_contribution_load_rows(
            employment_scope, contrib_year, contrib_month, contrib_department_id,
        )
    loan_rows = []
    if loan_load:
        loan_rows = _hdmf_loan_load_rows(
            employment_scope, loan_year, loan_month, loan_department_id,
        )

    page_title = f"HDMF {HDMF_SCOPE_LABELS[employment_scope]}"
    return render_template(
        'payroll/hdmf.html',
        employee=employee,
        departments=departments,
        selected_department=selected_department,
        year=year,
        month=month,
        hdmf_url_scope=url_scope,
        hdmf_employment_scope=employment_scope,
        page_title=page_title,
        contrib_year=contrib_year,
        contrib_month=contrib_month,
        contrib_selected_department=contrib_selected_department,
        contrib_rows=contrib_rows,
        contrib_loaded=contrib_load,
        show_contrib_upload=show_contrib_upload,
        has_contrib_data=has_contrib_data,
        loan_year=loan_year,
        loan_month=loan_month,
        loan_selected_department=loan_selected_department,
        loan_rows=loan_rows,
        loan_loaded=loan_load,
        show_loan_upload=show_loan_upload,
        has_loan_data=has_loan_data,
        hdmf_loan_types=HDMF_LOAN_TYPE_FIELDS,
        active_tab=_hdmf_active_tab(),
    )


@bp.route('/payroll/deductions/hdmf/<scope>')
@login_required
def payroll_deductions_hdmf(scope):
    return _render_hdmf_page(scope)


@bp.route('/payroll/deductions/hdmf/<scope>/contributions/preview', methods=['POST'])
@login_required
def payroll_deductions_hdmf_contributions_preview(scope):
    employment_scope = _hdmf_employment_scope(scope)
    if not employment_scope:
        abort(404)
    denied = _require_admin_or_hr()
    if denied:
        return denied
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'No file uploaded.'}), 400
    try:
        data = _parse_hdmf_contribution_xlsx(f.read(), employment_scope)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/payroll/deductions/hdmf/<scope>/contributions/upload-submit', methods=['POST'])
@login_required
def payroll_deductions_hdmf_contributions_upload_submit(scope):
    employment_scope = _hdmf_employment_scope(scope)
    if not employment_scope:
        abort(404)
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month.', 'error')
        return redirect(url_for('routes.payroll_deductions_hdmf', scope=scope, contrib_view='upload') + '#contribution')
    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []
    matched = [r for r in payload if r.get('matched') and r.get('employee_pk')]
    # De-duplicate rows in case the file contains repeated employee/program entries.
    # Keyed by (employee_id, membership_program) — for MP2/M2, include account no.
    # Last occurrence wins.
    dedup: dict[tuple[int, str], dict] = {}
    for r in matched:
        try:
            emp_id = int(r.get('employee_pk'))
        except Exception:
            continue
        program = (r.get('membership_program') or 'Unknown').strip()
        mp2_acct = (r.get('mp2_account_no') or '').strip()
        _, program_key, _ = _hdmf_membership_program_key(program, mp2_acct)
        r['membership_program'] = program_key
        dedup[(emp_id, program_key)] = r
    matched = list(dedup.values())

    if not matched:
        flash('No matched employees to save.', 'warning')
        return redirect(url_for('routes.payroll_deductions_hdmf', scope=scope, contrib_view='upload') + '#contribution')
    try:
        (HdmfContributionRecord.query
         .filter_by(year=year, month=month, employment_scope=employment_scope)
         .delete(synchronize_session=False))
        inserted = 0
        for r in matched:
            emp_id = int(r['employee_pk'])
            db.session.add(HdmfContributionRecord(
                employee_id=emp_id,
                employment_scope=employment_scope,
                classification=HDMF_CLASSIFICATION.get((employment_scope, 'contribution')),
                year=year,
                month=month,
                mid_no=(r.get('mid_no') or '').strip() or None,
                mp2_account_no=(r.get('mp2_account_no') or '').strip() or None,
                membership_program=(r.get('membership_program') or 'Unknown').strip(),
                percov=(r.get('percov') or '').strip() or None,
                er_share=_money_decimal(r.get('er_share')),
                ee_share=_money_decimal(r.get('ee_share')),
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'HDMF contribution file saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving HDMF contribution upload: {str(e)}', 'error')
        return redirect(url_for('routes.payroll_deductions_hdmf', scope=scope, contrib_view='upload') + '#contribution')
    return redirect(url_for(
        'routes.payroll_deductions_hdmf',
        scope=scope,
        contrib_year=year,
        contrib_month=month,
        contrib_view='load',
        contrib_uploaded=1,
    ) + '#contribution')


@bp.route('/payroll/deductions/hdmf/<scope>/contributions/deductions-submit', methods=['POST'])
@login_required
def payroll_deductions_hdmf_contributions_deductions_submit(scope):
    employment_scope = _hdmf_employment_scope(scope)
    if not employment_scope:
        abort(404)
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    dept_raw = (request.form.get('department_id') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month.', 'error')
        return redirect(url_for('routes.payroll_deductions_hdmf', scope=scope, contrib_view='load') + '#contribution')
    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None
    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []
    if not payload:
        flash('No contribution deduction rows to save.', 'warning')
        return redirect(url_for(
            'routes.payroll_deductions_hdmf', scope=scope,
            contrib_year=year, contrib_month=month, contrib_department_id=department_id or '',
            contrib_load=1, contrib_view='load',
        ) + '#contribution')
    try:
        employee_ids = {int(r.get('employee_pk')) for r in payload}
        for emp_id in employee_ids:
            (HdmfContributionDeduction.query
             .filter_by(employee_id=emp_id, year=year, month=month, employment_scope=employment_scope)
             .delete(synchronize_session=False))
        inserted = 0
        for r in payload:
            emp_id = int(r.get('employee_pk'))
            program = (r.get('membership_program') or '').strip()
            if not program:
                continue
            ps_amount = _money_decimal(r.get('ps_amount'))
            gs_amount = _money_decimal(r.get('gs_amount'))
            if employment_scope == 'jo_cos':
                mp2_amount = Decimal('0.00')
                month_amount = ps_amount.quantize(Decimal('0.01'))
                deductible_quincena = HDMF_JO_COS_DEDUCTIBLE_QUINCENA
            else:
                mp2_amount = _money_decimal(r.get('mp2_amount'))
                month_amount = (ps_amount + mp2_amount).quantize(Decimal('0.01'))
                deductible_quincena = HDMF_DEDUCTIBLE_QUINCENA
            emp = Employee.query.get(emp_id)
            if not emp:
                continue
            db.session.add(HdmfContributionDeduction(
                employee_id=emp_id,
                department_id=emp.department_id,
                employment_scope=employment_scope,
                classification=HDMF_CLASSIFICATION.get((employment_scope, 'contribution')),
                year=year,
                month=month,
                membership_program=program,
                ps_amount=ps_amount,
                gs_amount=gs_amount,
                mp2_amount=mp2_amount,
                month_amount=month_amount,
                deductible_quincena=deductible_quincena,
                deducted_amount=month_amount,
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'HDMF contribution deductions saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving HDMF contribution deductions: {str(e)}', 'error')
    return redirect(url_for(
        'routes.payroll_deductions_hdmf', scope=scope,
        contrib_year=year, contrib_month=month, contrib_department_id=department_id or '',
        contrib_load=1, contrib_view='load',
    ) + '#contribution')


@bp.route('/payroll/deductions/hdmf/<scope>/loans/preview', methods=['POST'])
@login_required
def payroll_deductions_hdmf_loans_preview(scope):
    employment_scope = _hdmf_employment_scope(scope)
    if not employment_scope:
        abort(404)
    denied = _require_admin_or_hr()
    if denied:
        return denied
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'No file uploaded.'}), 400
    try:
        data = _parse_hdmf_loan_xlsx(f.read(), employment_scope)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/payroll/deductions/hdmf/<scope>/loans/upload-submit', methods=['POST'])
@login_required
def payroll_deductions_hdmf_loans_upload_submit(scope):
    employment_scope = _hdmf_employment_scope(scope)
    if not employment_scope:
        abort(404)
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month.', 'error')
        return redirect(url_for('routes.payroll_deductions_hdmf', scope=scope, loan_view='upload') + '#loan')
    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []
    matched = [r for r in payload if r.get('matched') and r.get('employee_pk')]
    # De-duplicate rows in case the file contains repeated employee entries.
    # Keyed by employee_id. Last occurrence wins.
    dedup: dict[int, dict] = {}
    for r in matched:
        try:
            emp_id = int(r.get('employee_pk'))
        except Exception:
            continue
        dedup[emp_id] = r
    matched = list(dedup.values())

    if not matched:
        flash('No matched employees to save.', 'warning')
        return redirect(url_for('routes.payroll_deductions_hdmf', scope=scope, loan_view='upload') + '#loan')
    try:
        (HdmfLoanRecord.query
         .filter_by(year=year, month=month, employment_scope=employment_scope)
         .delete(synchronize_session=False))
        inserted = 0
        for r in matched:
            emp_id = int(r['employee_pk'])
            db.session.add(HdmfLoanRecord(
                employee_id=emp_id,
                employment_scope=employment_scope,
                classification=HDMF_CLASSIFICATION.get((employment_scope, 'loan')),
                year=year,
                month=month,
                mid_no=(r.get('mid_no') or '').strip() or None,
                mpl=_money_decimal(r.get('mpl')),
                salary=_money_decimal(r.get('salary')),
                housing=_money_decimal(r.get('housing')),
                safe=_money_decimal(r.get('safe')),
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'HDMF loan file saved ({inserted} employees).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving HDMF loan upload: {str(e)}', 'error')
        return redirect(url_for('routes.payroll_deductions_hdmf', scope=scope, loan_view='upload') + '#loan')
    return redirect(url_for(
        'routes.payroll_deductions_hdmf',
        scope=scope,
        loan_year=year,
        loan_month=month,
        loan_view='load',
        loan_uploaded=1,
    ) + '#loan')


@bp.route('/payroll/deductions/hdmf/<scope>/loans/deductions-submit', methods=['POST'])
@login_required
def payroll_deductions_hdmf_loans_deductions_submit(scope):
    employment_scope = _hdmf_employment_scope(scope)
    if not employment_scope:
        abort(404)
    denied = _require_admin_or_hr()
    if denied:
        return denied
    year_raw = (request.form.get('year') or '').strip()
    month_raw = (request.form.get('month') or '').strip()
    dept_raw = (request.form.get('department_id') or '').strip()
    rows_json = (request.form.get('rows_json') or '').strip()
    try:
        year = int(year_raw)
        month = int(month_raw)
        if month < 1 or month > 12:
            raise ValueError()
    except ValueError:
        flash('Invalid year or month.', 'error')
        return redirect(url_for('routes.payroll_deductions_hdmf', scope=scope, loan_view='load') + '#loan')
    department_id = None
    if dept_raw:
        try:
            department_id = int(dept_raw)
        except ValueError:
            department_id = None
    try:
        payload = json.loads(rows_json or '[]')
        if not isinstance(payload, list):
            payload = []
    except json.JSONDecodeError:
        payload = []
    if not payload:
        flash('No loan deduction rows to save.', 'warning')
        return redirect(url_for(
            'routes.payroll_deductions_hdmf', scope=scope,
            loan_year=year, loan_month=month, loan_department_id=department_id or '',
            loan_load=1, loan_view='load',
        ) + '#loan')
    try:
        employee_ids = {int(r.get('employee_pk')) for r in payload}
        for emp_id in employee_ids:
            (HdmfLoanDeduction.query
             .filter_by(employee_id=emp_id, year=year, month=month, employment_scope=employment_scope)
             .delete(synchronize_session=False))
        inserted = 0
        valid_types = {f[0] for f in HDMF_LOAN_TYPE_FIELDS}
        for r in payload:
            emp_id = int(r.get('employee_pk'))
            if employment_scope == 'plantilla':
                loan_type = (r.get('loan_type') or '').strip()
                if not loan_type:
                    continue
            else:
                loan_type = (r.get('loan_type') or '').strip().lower()
                if loan_type not in valid_types:
                    continue
            month_amount = _money_decimal(r.get('month_amount'))
            q1_amount = _money_decimal(r.get('q1_amount'))
            q2_amount = _money_decimal(r.get('q2_amount'))
            q1_on = str(r.get('q1_enabled', '1')).lower() in ('1', 'true', 'yes', 'on')
            q2_on = str(r.get('q2_enabled', '1')).lower() in ('1', 'true', 'yes', 'on')
            if employment_scope == 'jo_cos':
                q1_amount = month_amount
                q2_amount = Decimal('0.00')
                q1_on = True
                q2_on = False
            deducted_amount = (
                (q1_amount if q1_on else Decimal('0.00')) +
                (q2_amount if q2_on else Decimal('0.00'))
            ).quantize(Decimal('0.01'))
            emp = Employee.query.get(emp_id)
            if not emp:
                continue
            db.session.add(HdmfLoanDeduction(
                employee_id=emp_id,
                department_id=emp.department_id,
                employment_scope=employment_scope,
                classification=HDMF_CLASSIFICATION.get((employment_scope, 'loan')),
                year=year,
                month=month,
                loan_type=loan_type,
                month_amount=month_amount,
                q1_amount=q1_amount,
                q2_amount=q2_amount,
                q1_enabled=q1_on,
                q2_enabled=q2_on,
                deductible_quincena=HDMF_DEDUCTIBLE_QUINCENA,
                deducted_amount=deducted_amount,
                created_by_user_id=getattr(current_user, 'id', None),
            ))
            inserted += 1
        db.session.commit()
        flash(f'HDMF loan deductions saved ({inserted} rows).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving HDMF loan deductions: {str(e)}', 'error')
    return redirect(url_for(
        'routes.payroll_deductions_hdmf', scope=scope,
        loan_year=year, loan_month=month, loan_department_id=department_id or '',
        loan_load=1, loan_view='load',
    ) + '#loan')


@bp.route('/payroll/worktime-summary')
@login_required
def payroll_worktime_summary():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _worktime_summary_parse_filters(request.args)
    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    employees = _worktime_summary_eligible_employees(filters)
    result = _worktime_summary_result(filters)
    filter_label = _worktime_summary_filter_label(filters, dept_by_id)
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'payroll/worktime_summary.html',
        employee=employee,
        rows=result['rows'],
        view_mode=result['view_mode'],
        departments=departments,
        employees=employees,
        filters=filters,
        filter_label=filter_label,
        total_net=_mins_to_hhmm(result['total_net_mins']),
        row_count=len(result['rows']),
    )


@bp.route('/payroll/worktime-summary/export')
@login_required
def payroll_worktime_summary_export():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _worktime_summary_parse_filters(request.args)
    fmt = (request.args.get('format') or 'csv').strip().lower()
    dept_by_id = {d.id: d for d in Department.query.all()}
    period_label = _worktime_summary_filter_label(filters, dept_by_id)
    result = _worktime_summary_result(filters)
    rows = result['rows']
    view_mode = result['view_mode']
    base = (
        f'worktime_summary_{filters["year"]}_{filters["month"]:02d}_'
        f'Q{filters["quincena"]}'
    )
    if filters['department_id']:
        base += f'_dept{filters["department_id"]}'
    if filters.get('employee_type'):
        base += f'_{filters["employee_type"]}'
    if filters['employee_id']:
        base += f'_emp{filters["employee_id"]}'
    base += '_daily' if view_mode == 'daily' else '_by_employee'

    if fmt == 'csv':
        data = _worktime_summary_csv_bytes(rows, period_label, view_mode)
        return Response(
            data,
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename={base}.csv'},
        )
    if fmt == 'pdf':
        buf = _worktime_summary_pdf_bytes(rows, period_label, view_mode)
        return send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'{base}.pdf',
        )
    flash('Invalid export format. Use csv or pdf.', 'error')
    return redirect(url_for('routes.payroll_worktime_summary', **filters))


@bp.route('/payroll/jo-cos')
@login_required
def payroll_jo_cos():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _jo_cos_payroll_parse_filters(request.args)
    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    employee = current_user.employee if current_user.employee else None

    rows = []
    totals = {}
    period_label = ''
    department_name = ''
    dept_selected = False

    if filters['loaded'] and filters['department_id']:
        try:
            dept_id = int(filters['department_id'])
        except ValueError:
            flash('Select a valid department.', 'error')
            return redirect(url_for('routes.payroll_jo_cos'))
        dept = dept_by_id.get(dept_id)
        if not dept:
            flash('Department not found.', 'error')
            return redirect(url_for('routes.payroll_jo_cos'))
        dept_selected = True
        department_name = dept.name or f'Department #{dept_id}'
        period_label = _jo_cos_payroll_period_label(filters, department_name)
        rows, totals = _jo_cos_payroll_rows(filters)
        payslip_count = JoCosPayslip.query.filter_by(
            year=filters['year'],
            month=filters['month'],
            quincena_half=filters['quincena'],
            department_id=dept_id,
        ).count()
    elif filters['loaded']:
        flash('Select a department before loading payroll.', 'error')
        payslip_count = 0
    else:
        payslip_count = 0

    if not dept_selected:
        payslip_count = 0

    return render_template(
        'payroll/jo_cos.html',
        employee=employee,
        departments=departments,
        filters=filters,
        rows=rows,
        totals=totals,
        period_label=period_label,
        department_name=department_name,
        dept_selected=dept_selected,
        row_count=len(rows),
        payslip_count=payslip_count,
    )


@bp.route('/payroll/jo-cos/export')
@login_required
def payroll_jo_cos_export():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _jo_cos_payroll_parse_filters(request.args)
    filters['loaded'] = True
    fmt = (request.args.get('format') or 'pdf').strip().lower()
    if not filters['department_id']:
        flash('Select a department before exporting payroll.', 'error')
        return redirect(url_for('routes.payroll_jo_cos'))
    try:
        dept_id = int(filters['department_id'])
    except ValueError:
        flash('Select a valid department.', 'error')
        return redirect(url_for('routes.payroll_jo_cos'))
    dept = Department.query.get(dept_id)
    if not dept:
        flash('Department not found.', 'error')
        return redirect(url_for('routes.payroll_jo_cos'))

    rows, totals = _jo_cos_payroll_rows(filters)
    department_name = dept.name or f'Department #{dept_id}'
    period_label = _jo_cos_payroll_period_label(filters, department_name)
    base = (
        f'jo_cos_payroll_{filters["year"]}_{filters["month"]:02d}_'
        f'Q{filters["quincena"]}_dept{dept_id}'
    )

    if fmt == 'pdf':
        buf = _jo_cos_payroll_pdf_bytes(rows, totals, period_label, department_name)
        return send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'{base}.pdf',
        )
    flash('Invalid export format. Use pdf.', 'error')
    return redirect(url_for(
        'routes.payroll_jo_cos',
        year=filters['year'],
        month=filters['month'],
        quincena=filters['quincena'],
        department_id=filters['department_id'],
        load=1,
    ))


@bp.route('/payroll/jo-cos/payslips/generate', methods=['POST'])
@login_required
def payroll_jo_cos_payslips_generate():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _jo_cos_payroll_parse_filters(request.form)
    filters['loaded'] = True
    if not filters['department_id']:
        flash('Select a department before generating payslips.', 'error')
        return redirect(url_for('routes.payroll_jo_cos'))
    try:
        dept_id = int(filters['department_id'])
    except ValueError:
        flash('Select a valid department.', 'error')
        return redirect(url_for('routes.payroll_jo_cos'))
    dept = Department.query.get(dept_id)
    if not dept:
        flash('Department not found.', 'error')
        return redirect(url_for('routes.payroll_jo_cos'))
    department_name = dept.name or f'Department #{dept_id}'
    try:
        count = _jo_cos_payslips_generate(
            filters, department_name, getattr(current_user, 'id', None),
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating payslips: {e}', 'error')
        return redirect(url_for(
            'routes.payroll_jo_cos',
            year=filters['year'], month=filters['month'],
            quincena=filters['quincena'], department_id=filters['department_id'], load=1,
        ))
    if count:
        flash(f'Generated {count} payslip(s) for {_jo_cos_payslip_period_label(filters["year"], filters["month"], filters["quincena"])}.', 'success')
    else:
        flash('No payroll rows to generate payslips from. Load payroll first.', 'warning')
    return redirect(url_for(
        'routes.payroll_jo_cos',
        year=filters['year'], month=filters['month'],
        quincena=filters['quincena'], department_id=filters['department_id'], load=1,
    ))


@bp.route('/payroll/jo-cos/payslips/export')
@login_required
def payroll_jo_cos_payslips_export():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _jo_cos_payroll_parse_filters(request.args)
    if not filters['department_id']:
        flash('Select a department before exporting payslips.', 'error')
        return redirect(url_for('routes.payroll_jo_cos'))
    try:
        dept_id = int(filters['department_id'])
    except ValueError:
        flash('Select a valid department.', 'error')
        return redirect(url_for('routes.payroll_jo_cos'))
    slips = (
        JoCosPayslip.query.filter_by(
            year=filters['year'],
            month=filters['month'],
            quincena_half=filters['quincena'],
            department_id=dept_id,
        )
        .join(Employee)
        .order_by(Employee.last_name.asc(), Employee.first_name.asc())
        .all()
    )
    if not slips:
        flash('No payslips found for this period. Click Generate Payslip first.', 'warning')
        return redirect(url_for(
            'routes.payroll_jo_cos',
            year=filters['year'], month=filters['month'],
            quincena=filters['quincena'], department_id=filters['department_id'], load=1,
        ))
    buf = _jo_cos_payslip_pdf_bytes(slips)
    base = (
        f'jo_cos_payslips_{filters["year"]}_{filters["month"]:02d}_'
        f'Q{filters["quincena"]}_dept{dept_id}'
    )
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{base}.pdf')


@bp.route('/payroll/jo-cos/my-payslips')
@login_required
def payroll_jo_cos_my_payslips():
    emp = _current_employee_for_user()
    if not emp:
        flash('Employee record not linked to your account.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    if not _is_jo_cos_employee(emp):
        flash('Payslips are available for JO/COS employees only.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    selected_year = (request.args.get('year') or '').strip()
    q = JoCosPayslip.query.filter_by(employee_id=emp.id)
    if selected_year:
        try:
            q = q.filter(JoCosPayslip.year == int(selected_year))
        except (TypeError, ValueError):
            selected_year = ''
    slips = (
        q.order_by(
            JoCosPayslip.year.desc(),
            JoCosPayslip.month.desc(),
            JoCosPayslip.quincena_half.desc(),
        ).all()
    )
    available_years = [
        y for (y,) in (
            db.session.query(JoCosPayslip.year)
            .filter_by(employee_id=emp.id)
            .distinct()
            .order_by(JoCosPayslip.year.desc())
            .all()
        )
    ]
    rows = []
    for slip in slips:
        d = _jo_cos_payslip_view_dict(slip)
        d['period_short'] = _jo_cos_payslip_period_label(slip.year, slip.month, slip.quincena_half)
        rows.append(d)
    return render_template(
        'payroll/jo_cos_my_payslips.html',
        employee=emp,
        payslips=rows,
        available_years=available_years,
        selected_year=selected_year,
    )


@bp.route('/payroll/jo-cos/payslips/<int:payslip_id>')
@login_required
def payroll_jo_cos_payslip_view(payslip_id: int):
    slip = JoCosPayslip.query.get_or_404(payslip_id)
    if not _can_view_jo_cos_payslip(slip):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'payroll/jo_cos_payslip.html',
        employee=employee,
        payslip=_jo_cos_payslip_view_dict(slip),
        slip_id=slip.id,
        is_admin_view=(getattr(current_user, 'role', None) or '').strip().lower() in ('admin', 'hr'),
    )


@bp.route('/payroll/jo-cos/payslips/<int:payslip_id>/pdf')
@login_required
def payroll_jo_cos_payslip_pdf(payslip_id: int):
    slip = JoCosPayslip.query.get_or_404(payslip_id)
    if not _can_view_jo_cos_payslip(slip):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    buf = _jo_cos_payslip_pdf_bytes([slip])
    fname = (
        f'payslip_{slip.employee_no or slip.employee_id}_'
        f'{slip.year}_{slip.month:02d}_Q{slip.quincena_half}.pdf'
    )
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=fname)


@bp.route('/payroll/plantilla')
@login_required
def payroll_plantilla():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _payroll_quincena_parse_filters(request.args)
    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    employee = current_user.employee if current_user.employee else None

    rows = []
    totals = {}
    payroll_layout = _plantilla_payroll_empty_layout()
    period_label = ''
    service_period = ''
    department_name = ''
    dept_selected = False

    if filters['loaded'] and filters['department_id']:
        try:
            dept_id = int(filters['department_id'])
        except ValueError:
            flash('Select a valid department.', 'error')
            return redirect(url_for('routes.payroll_plantilla'))
        dept = dept_by_id.get(dept_id)
        if not dept:
            flash('Department not found.', 'error')
            return redirect(url_for('routes.payroll_plantilla'))
        dept_selected = True
        department_name = dept.name or f'Department #{dept_id}'
        period_label = _plantilla_payroll_period_label(filters, department_name)
        service_period = _plantilla_payroll_service_period_label(
            filters['year'], filters['month'], filters['quincena'],
        )
        rows, totals, payroll_layout = _plantilla_payroll_rows(filters)
    elif filters['loaded']:
        flash('Select a department before loading payroll.', 'error')

    return render_template(
        'payroll/plantilla.html',
        employee=employee,
        departments=departments,
        filters=filters,
        rows=rows,
        totals=totals,
        period_label=period_label,
        service_period=service_period,
        department_name=department_name,
        dept_selected=dept_selected,
        row_count=len(rows),
        deduction_sections=payroll_layout['deduction_sections'],
        display_deduction_keys=payroll_layout['display_keys'],
        deduction_col_count=payroll_layout['deduction_col_count'],
        gov_share_sections=payroll_layout['gov_share_sections'],
        display_gov_share_keys=payroll_layout['gov_share_keys'],
        gov_share_col_count=payroll_layout['gov_share_col_count'],
    )


@bp.route('/payroll/plantilla/export')
@login_required
def payroll_plantilla_export():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _payroll_quincena_parse_filters(request.args)
    filters['loaded'] = True
    fmt = (request.args.get('format') or 'pdf').strip().lower()
    if not filters['department_id']:
        flash('Select a department before exporting payroll.', 'error')
        return redirect(url_for('routes.payroll_plantilla'))
    try:
        dept_id = int(filters['department_id'])
    except ValueError:
        flash('Select a valid department.', 'error')
        return redirect(url_for('routes.payroll_plantilla'))
    dept = Department.query.get(dept_id)
    if not dept:
        flash('Department not found.', 'error')
        return redirect(url_for('routes.payroll_plantilla'))

    rows, totals, payroll_layout = _plantilla_payroll_rows(filters)
    department_name = dept.name or f'Department #{dept_id}'
    service_period = _plantilla_payroll_service_period_label(
        filters['year'], filters['month'], filters['quincena'],
    )
    _, end_d = _quincena_date_range(filters['year'], filters['month'], filters['quincena'])
    base = (
        f'plantilla_payroll_{filters["year"]}_{filters["month"]:02d}_'
        f'Q{filters["quincena"]}_dept{dept_id}'
    )
    if fmt == 'pdf':
        buf = _plantilla_payroll_pdf_bytes(
            rows,
            totals,
            service_period,
            department_name,
            end_d,
            display_keys=payroll_layout['display_keys'],
            key_labels=payroll_layout['deduction_key_labels'],
            gov_share_keys=payroll_layout['gov_share_keys'],
            gov_share_labels=_plantilla_payroll_gov_share_key_labels(),
        )
        return send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'{base}.pdf',
        )
    flash('Invalid export format. Use pdf.', 'error')
    return redirect(url_for(
        'routes.payroll_plantilla',
        year=filters['year'],
        month=filters['month'],
        quincena=filters['quincena'],
        department_id=filters['department_id'],
        load=1,
    ))


def _plantilla_payslip_upsert_from_payroll_row(
    row: dict,
    *,
    year: int,
    month: int,
    quincena: str,
    department_id: int | None,
    department_name: str,
    generated_by_user_id: int | None,
    layout: dict,
) -> PlantillaPayslip:
    deductions = row.get('deductions') or {}
    gov_share = row.get('gov_share') or {}

    def _to_json_money_map(m: dict) -> dict:
        out = {}
        for k, v in (m or {}).items():
            try:
                out[str(k)] = str(_money_decimal(v).quantize(Decimal('0.01')))
            except Exception:
                out[str(k)] = '0.00'
        return out

    return PlantillaPayslip(
        employee_id=row['employee_pk'],
        department_id=department_id,
        year=year,
        month=month,
        quincena_half=str(quincena),
        employee_name=row.get('employee_name') or '',
        employee_no=row.get('employee_no') or '',
        designation=row.get('designation') or '—',
        department_name=department_name,
        monthly_rate=_money_decimal(row.get('monthly_rate') or 0),
        accrued=_money_decimal(row.get('accrued') or 0),
        net_amount=_money_decimal(row.get('net_amount') or 0),
        deductions=_to_json_money_map(deductions),
        gov_share=_to_json_money_map(gov_share),
        layout=layout or {},
        generated_at=datetime.utcnow(),
        generated_by_user_id=generated_by_user_id,
    )


@bp.route('/payroll/plantilla/payslips/generate', methods=['POST'])
@login_required
def payroll_plantilla_payslips_generate():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    filters = _payroll_quincena_parse_filters(request.form)
    filters['loaded'] = True
    if not filters['department_id']:
        flash('Select a department before generating payslips.', 'error')
        return redirect(url_for('routes.payroll_plantilla'))
    try:
        dept_id = int(filters['department_id'])
    except ValueError:
        flash('Select a valid department.', 'error')
        return redirect(url_for('routes.payroll_plantilla'))
    dept = Department.query.get(dept_id)
    if not dept:
        flash('Department not found.', 'error')
        return redirect(url_for('routes.payroll_plantilla'))
    department_name = dept.name or f'Department #{dept_id}'

    year, month, quincena = filters['year'], filters['month'], filters['quincena']
    try:
        rows, _totals, payroll_layout = _plantilla_payroll_rows(filters)
        if not rows:
            flash('No payroll rows to generate payslips from. Load payroll first.', 'warning')
            return redirect(url_for(
                'routes.payroll_plantilla',
                year=year, month=month, quincena=quincena, department_id=dept_id, load=1,
            ))

        emp_ids = [r['employee_pk'] for r in rows]
        existing = {
            p.employee_id: p
            for p in PlantillaPayslip.query.filter(
                PlantillaPayslip.employee_id.in_(emp_ids),
                PlantillaPayslip.year == year,
                PlantillaPayslip.month == month,
                PlantillaPayslip.quincena_half == str(quincena),
            ).all()
        }
        layout_payload = {
            'deduction_keys': payroll_layout.get('display_keys', []),
            'deduction_labels': payroll_layout.get('deduction_key_labels', _plantilla_payroll_deduction_key_labels()),
            'gov_share_keys': payroll_layout.get('gov_share_keys', []),
            'gov_share_labels': _plantilla_payroll_gov_share_key_labels(),
        }

        notify_rows: list[tuple[int, int]] = []
        count = 0
        for r in rows:
            emp_obj = Employee.query.get(r['employee_pk'])
            if emp_obj and str(quincena) == '1':
                post_lwop_payroll_settlement(
                    emp_obj, year, month, str(quincena),
                    created_by_user_id=getattr(current_user, 'id', None),
                )
            slip = existing.get(r['employee_pk'])
            if slip:
                slip.department_id = dept_id
                slip.employee_name = r.get('employee_name') or slip.employee_name
                slip.employee_no = r.get('employee_no') or ''
                slip.designation = r.get('designation') or '—'
                slip.department_name = department_name
                slip.monthly_rate = _money_decimal(r.get('monthly_rate') or 0)
                slip.accrued = _money_decimal(r.get('accrued') or 0)
                slip.net_amount = _money_decimal(r.get('net_amount') or 0)
                slip.deductions = {k: str(_money_decimal(v).quantize(Decimal('0.01'))) for k, v in (r.get('deductions') or {}).items()}
                slip.gov_share = {k: str(_money_decimal(v).quantize(Decimal('0.01'))) for k, v in (r.get('gov_share') or {}).items()}
                slip.layout = layout_payload
                slip.generated_at = datetime.utcnow()
                slip.generated_by_user_id = getattr(current_user, 'id', None)
                notify_rows.append((slip.employee_id, slip.id))
            else:
                new_slip = _plantilla_payslip_upsert_from_payroll_row(
                    r,
                    year=year,
                    month=month,
                    quincena=str(quincena),
                    department_id=dept_id,
                    department_name=department_name,
                    generated_by_user_id=getattr(current_user, 'id', None),
                    layout=layout_payload,
                )
                db.session.add(new_slip)
                db.session.flush()
                notify_rows.append((new_slip.employee_id, new_slip.id))
            count += 1

        period = _plantilla_payslip_period_label(year, month, str(quincena))
        for emp_id, slip_id in notify_rows:
            try:
                emp = Employee.query.get(emp_id)
                if emp and emp.user_id:
                    _create_hrms_notification(
                        emp.user_id,
                        'Payslip available',
                        f'Your plantilla payslip is ready for {period}.',
                        link_url=url_for('routes.payroll_plantilla_my_payslips', year=year),
                        related_type='plantilla_payslip',
                        related_id=slip_id,
                    )
            except Exception:
                pass

        db.session.commit()
        flash(f'Generated {count} payslip(s) for {period}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating payslips: {e}', 'error')
    return redirect(url_for(
        'routes.payroll_plantilla',
        year=year, month=month, quincena=quincena, department_id=dept_id, load=1,
    ))


@bp.route('/payroll/plantilla/my-payslips')
@login_required
def payroll_plantilla_my_payslips():
    emp = _current_employee_for_user()
    if not emp:
        flash('Employee record not linked to your account.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    if _is_jo_cos_employee(emp):
        flash('Plantilla payslips are not available for JO/COS employees.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))

    selected_year = (request.args.get('year') or '').strip()
    q = PlantillaPayslip.query.filter_by(employee_id=emp.id)
    if selected_year:
        try:
            q = q.filter(PlantillaPayslip.year == int(selected_year))
        except (TypeError, ValueError):
            selected_year = ''
    slips = (
        q.order_by(
            PlantillaPayslip.year.desc(),
            PlantillaPayslip.month.desc(),
            PlantillaPayslip.quincena_half.desc(),
        ).all()
    )
    available_years = [
        y for (y,) in (
            db.session.query(PlantillaPayslip.year)
            .filter_by(employee_id=emp.id)
            .distinct()
            .order_by(PlantillaPayslip.year.desc())
            .all()
        )
    ]

    def _period_short(s: PlantillaPayslip) -> str:
        return _plantilla_payslip_period_label(s.year, s.month, s.quincena_half)

    rows = []
    for s in slips:
        rows.append({
            'id': s.id,
            'year': s.year,
            'month': s.month,
            'quincena': s.quincena_half,
            'department_name': s.department_name or '',
            'net_amount_display': _money_str(_money_decimal(s.net_amount)),
            'generated_at': s.generated_at,
            'period_short': _period_short(s),
        })

    return render_template(
        'payroll/plantilla_my_payslips.html',
        employee=emp,
        payslips=rows,
        available_years=available_years,
        selected_year=selected_year,
    )


@bp.route('/payroll/plantilla/payslips/<int:payslip_id>')
@login_required
def payroll_plantilla_payslip_view(payslip_id: int):
    slip = PlantillaPayslip.query.get_or_404(payslip_id)
    if not _can_view_plantilla_payslip(slip):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))

    labels = (slip.layout or {}).get('deduction_labels') or _plantilla_payroll_deduction_key_labels()
    ded_map = slip.deductions or {}
    ded_rows = []
    for key, amt_str in ded_map.items():
        try:
            amt = Decimal(str(amt_str))
        except Exception:
            amt = Decimal('0.00')
        if amt == 0:
            continue
        ded_rows.append({'key': key, 'label': labels.get(key, key), 'amount_display': _money_str(amt)})
    ded_rows.sort(key=lambda r: r['label'])

    gov_labels = (slip.layout or {}).get('gov_share_labels') or _plantilla_payroll_gov_share_key_labels()
    gov_map = slip.gov_share or {}
    gov_rows = []
    for key, amt_str in gov_map.items():
        try:
            amt = Decimal(str(amt_str))
        except Exception:
            amt = Decimal('0.00')
        if amt == 0:
            continue
        gov_rows.append({'key': key, 'label': gov_labels.get(key, key), 'amount_display': _money_str(amt)})
    gov_rows.sort(key=lambda r: r['label'])

    payload = {
        'id': slip.id,
        'year': slip.year,
        'month': slip.month,
        'quincena': slip.quincena_half,
        'department_id': slip.department_id,
        'employee_name': slip.employee_name,
        'employee_no': slip.employee_no or '',
        'designation': slip.designation or '—',
        'department_name': slip.department_name or '',
        'period_label': _plantilla_payslip_period_label(slip.year, slip.month, slip.quincena_half),
        'monthly_rate_display': _money_str(_money_decimal(slip.monthly_rate)),
        'accrued_display': _money_str(_money_decimal(slip.accrued)),
        'net_amount_display': _money_str(_money_decimal(slip.net_amount)),
        'generated_at': slip.generated_at,
        'deductions_rows': ded_rows,
        'gov_share_rows': gov_rows,
    }

    return render_template(
        'payroll/plantilla_payslip_view.html',
        employee=current_user.employee if current_user.employee else None,
        payslip=payload,
        is_admin_view=(getattr(current_user, 'role', None) or '').strip().lower() in ('admin', 'hr'),
    )


def _report_employees_access_denied():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr', 'payroll_maker'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    return None


def _report_employees_base_query():
    return Employee.query.options(
        db.joinedload(Employee.department),
        db.joinedload(Employee.jo_cos_designation),
    )


def _report_employees_apply_filters(q, department_id: str, appointment: str):
    did = (department_id or '').strip()
    if did:
        try:
            q = q.filter(Employee.department_id == int(did))
        except (TypeError, ValueError):
            pass
    apt = (appointment or 'all').strip()
    if not apt or apt.lower() == 'all':
        return q
    if apt.lower() == 'plantilla':
        return q.filter(Employee.status_of_appointment.in_(REPORT_EMPLOYEE_PLANTILLA_STATUSES))
    if apt.lower() == 'non_plantilla':
        return q.filter(Employee.status_of_appointment.in_(REPORT_EMPLOYEE_NON_PLANTILLA_STATUSES))
    apt_lower = apt.lower()
    for canon in REPORT_EMPLOYEE_PLANTILLA_STATUSES + REPORT_EMPLOYEE_NON_PLANTILLA_STATUSES:
        if canon.lower() == apt_lower:
            return q.filter(Employee.status_of_appointment == canon)
    return q


def _report_employee_display_position(emp):
    if getattr(emp, 'jo_cos_designation', None) and emp.jo_cos_designation:
        d = (emp.jo_cos_designation.designation or '').strip()
        if d:
            return d
    return (emp.position or '').strip() or 'N/A'


def _report_employees_row_dict(emp):
    return {
        'employee_id': emp.employee_id or '',
        'full_name': emp.full_name,
        'position': _report_employee_display_position(emp),
        'department': emp.department.name if emp.department else 'N/A',
        'status_of_appointment': (emp.status_of_appointment or '').strip(),
        'employment_status': (emp.status or '').strip(),
        'appointment_date': emp.appointment_date.strftime('%Y-%m-%d') if emp.appointment_date else '',
    }


def _report_employees_filter_label(department_id: str, appointment: str, dept_by_id: dict) -> str:
    parts = []
    did = (department_id or '').strip()
    if did:
        try:
            dep = dept_by_id.get(int(did))
            if dep:
                parts.append(f'Department: {dep.name}')
        except (TypeError, ValueError):
            pass
    apt = (appointment or 'all').strip()
    al = apt.lower()
    if not al or al == 'all':
        parts.append('Appointment: all')
    elif al == 'plantilla':
        parts.append(
            'Appointment: plantilla (Permanent, Casual, Contractual, Temporary, Coterminus, Probational, Elective)'
        )
    elif al == 'non_plantilla':
        parts.append('Appointment: non-plantilla (Job Order, Contract of Service)')
    else:
        parts.append(f'Appointment: {apt}')
    return ' | '.join(parts)


def _report_employees_csv_bytes(rows, filter_label: str) -> bytes:
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['Employees list', filter_label])
    w.writerow(['Employee ID', 'Full name', 'Position', 'Department', 'Status of appointment', 'Employment status', 'Appointment date'])
    for r in rows:
        w.writerow([
            r.get('employee_id', ''),
            r.get('full_name', ''),
            r.get('position', ''),
            r.get('department', ''),
            r.get('status_of_appointment', ''),
            r.get('employment_status', ''),
            r.get('appointment_date', ''),
        ])
    return output.getvalue().encode('utf-8')


def _report_employees_xlsx_bytes(rows) -> io.BytesIO:
    import pandas as pd

    cols = [
        'employee_id', 'full_name', 'position', 'department',
        'status_of_appointment', 'employment_status', 'appointment_date',
    ]
    df = pd.DataFrame([{k: r.get(k, '') for k in cols} for r in rows], columns=cols)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Employees')
    buf.seek(0)
    return buf


def _split_pdf_label_lines(text: str, width: int, max_lines: int = 6):
    t = (text or '').strip()
    if not t:
        return ['']
    lines = []
    while t and len(lines) < max_lines:
        if len(t) <= width:
            lines.append(t)
            break
        lines.append(t[:width])
        t = t[width:]
    if t and lines:
        lines[-1] = lines[-1][: max(0, width - 3)] + '...'
    return lines


def _report_employees_pdf_bytes(rows, filter_label: str) -> io.BytesIO:
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    page_w, page_h = landscape(letter)
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    def trunc(s, n):
        s = str(s or '')
        return (s[: n - 3] + '...') if len(s) > n else s

    y = page_h - 36
    c.setFont('Helvetica-Bold', 11)
    c.drawString(28, y, 'HRMS — Employees list')
    y -= 14
    c.setFont('Helvetica', 8.5)
    for line in _split_pdf_label_lines(filter_label, 130):
        c.drawString(28, y, line)
        y -= 11
        if y < 50:
            c.showPage()
            y = page_h - 36
            c.setFont('Helvetica', 8.5)
    y -= 6
    c.setFont('Helvetica-Bold', 7.5)
    x_id, x_name, x_pos, x_dept, x_soa, x_est = 28, 78, 218, 348, 448, 518
    c.drawString(x_id, y, 'Emp. ID')
    c.drawString(x_name, y, 'Name')
    c.drawString(x_pos, y, 'Position')
    c.drawString(x_dept, y, 'Department')
    c.drawString(x_soa, y, 'Appointment')
    c.drawString(x_est, y, 'Emp. status')
    y -= 12
    c.setFont('Helvetica', 7.5)
    for r in rows:
        if y < 34:
            c.showPage()
            y = page_h - 36
            c.setFont('Helvetica-Bold', 7.5)
            c.drawString(x_id, y, 'Emp. ID')
            c.drawString(x_name, y, 'Name')
            c.drawString(x_pos, y, 'Position')
            c.drawString(x_dept, y, 'Department')
            c.drawString(x_soa, y, 'Appointment')
            c.drawString(x_est, y, 'Emp. status')
            y -= 12
            c.setFont('Helvetica', 7.5)
        c.drawString(x_id, y, trunc(r.get('employee_id', ''), 10))
        c.drawString(x_name, y, trunc(r.get('full_name', ''), 24))
        c.drawString(x_pos, y, trunc(r.get('position', ''), 22))
        c.drawString(x_dept, y, trunc(r.get('department', ''), 16))
        c.drawString(x_soa, y, trunc(r.get('status_of_appointment', ''), 14))
        c.drawString(x_est, y, trunc(r.get('employment_status', ''), 12))
        y -= 11
    c.save()
    buf.seek(0)
    return buf


@bp.route('/reports/employees-list')
@login_required
def report_employees_list():
    denied = _report_employees_access_denied()
    if denied:
        return denied
    department_id = (request.args.get('department_id') or '').strip()
    appointment = (request.args.get('appointment') or 'all').strip()
    q = _report_employees_base_query()
    q = _report_employees_apply_filters(q, department_id, appointment)
    employees = q.order_by(Employee.last_name, Employee.first_name).all()
    departments = Department.query.order_by(Department.name.asc()).all()
    dept_by_id = {d.id: d for d in departments}
    employee = current_user.employee if current_user.employee else None
    filter_label = _report_employees_filter_label(department_id, appointment, dept_by_id)
    return render_template(
        'reports/employees_list.html',
        employees=employees,
        departments=departments,
        employee=employee,
        filter_department_id=department_id,
        filter_appointment=appointment,
        filter_label=filter_label,
        plantilla_statuses=REPORT_EMPLOYEE_PLANTILLA_STATUSES,
        non_plantilla_statuses=REPORT_EMPLOYEE_NON_PLANTILLA_STATUSES,
    )


@bp.route('/reports/service-records')
@login_required
def report_service_records():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    department_id = (request.args.get('department_id') or '').strip()
    status = (request.args.get('status') or 'active').strip().lower()
    q = _report_employees_base_query()
    if department_id:
        try:
            q = q.filter(Employee.department_id == int(department_id))
        except (TypeError, ValueError):
            pass
    if status and status != 'all':
        q = q.filter(Employee.status == status)
    employees = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()
    departments = Department.query.order_by(Department.name.asc()).all()
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'reports/service_records.html',
        employees=employees,
        departments=departments,
        employee=employee,
        filter_department_id=department_id,
        filter_status=status if status in ('active', 'inactive', 'separated', 'all') else 'active',
    )


@bp.route('/reports/employee-id-cards')
@login_required
def report_employee_id_cards():
    """Admin/HR: pick employees and print LGU Jagna ID cards (individual or batch)."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    department_id = (request.args.get('department_id') or '').strip()
    status = (request.args.get('status') or 'active').strip().lower()
    id_format = (request.args.get('id_format') or 'all').strip().lower()
    q = _report_employees_base_query()
    if department_id:
        try:
            q = q.filter(Employee.department_id == int(department_id))
        except (TypeError, ValueError):
            pass
    if status and status != 'all':
        q = q.filter(Employee.status == status)
    employees = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()
    from app.employee_id_photo_service import employee_has_id_photo
    from app.employee_id_card_service import id_card_format_label, is_jo_cos_employee
    if id_format == 'jo_cos':
        employees = [e for e in employees if is_jo_cos_employee(e)]
    elif id_format == 'plantilla':
        employees = [e for e in employees if not is_jo_cos_employee(e)]
    photo_flags = {e.id: employee_has_id_photo(e) for e in employees}
    format_labels = {e.id: id_card_format_label(e) for e in employees}
    departments = Department.query.order_by(Department.name.asc()).all()
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'reports/employee_id_cards.html',
        employees=employees,
        departments=departments,
        employee=employee,
        photo_flags=photo_flags,
        format_labels=format_labels,
        filter_department_id=department_id,
        filter_status=status if status in ('active', 'inactive', 'separated', 'all') else 'active',
        filter_id_format=id_format if id_format in ('all', 'jo_cos', 'plantilla') else 'all',
    )


@bp.route('/reports/employee-id-cards/batch.pdf', methods=['POST'])
@login_required
def report_employee_id_cards_batch_pdf():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    raw_ids = request.form.getlist('employee_ids')
    ids = []
    for raw in raw_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        flash('Select at least one employee to print ID cards.', 'error')
        return redirect(url_for('routes.report_employee_id_cards'))
    employees = (
        Employee.query
        .filter(Employee.id.in_(ids))
        .order_by(Employee.last_name.asc(), Employee.first_name.asc())
        .all()
    )
    if not employees:
        flash('No matching employees found.', 'error')
        return redirect(url_for('routes.report_employee_id_cards'))
    from app.employee_id_card_service import build_employee_id_cards_batch_pdf
    try:
        pdf_bytes = build_employee_id_cards_batch_pdf(employees)
    except Exception as exc:
        flash(f'Could not generate ID cards: {exc}', 'error')
        return redirect(url_for('routes.report_employee_id_cards'))
    fname = f'LGU_Jagna_ID_batch_{len(employees)}.pdf'
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=fname,
    )


@bp.route('/employees/<int:id>/id-card.pdf')
@login_required
def employee_id_card_pdf(id: int):
    """Admin/HR: download one employee's printable LGU Jagna ID card."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp = Employee.query.get_or_404(id)
    from app.employee_id_card_service import build_employee_id_card_pdf, id_card_download_name
    try:
        pdf_bytes = build_employee_id_card_pdf(emp)
    except Exception as exc:
        flash(f'Could not generate ID card: {exc}', 'error')
        return redirect(request.referrer or url_for('routes.employees_list'))
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=id_card_download_name(emp),
    )


@bp.route('/reports/leave-credits')
@login_required
def report_leave_credits():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    department_id = (request.args.get('department_id') or '').strip()
    status = (request.args.get('status') or 'active').strip().lower()
    q = _report_employees_base_query().filter(
        Employee.status_of_appointment.in_(LEAVE_CREDITS_STATUSES)
    )
    if department_id:
        try:
            q = q.filter(Employee.department_id == int(department_id))
        except (TypeError, ValueError):
            pass
    if status and status != 'all':
        q = q.filter(Employee.status == status)
    employees = q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()
    departments = Department.query.order_by(Department.name.asc()).all()
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'reports/leave_credits.html',
        employees=employees,
        departments=departments,
        employee=employee,
        filter_department_id=department_id,
        filter_status=status if status in ('active', 'inactive', 'separated', 'all') else 'active',
    )


@bp.route('/reports/employees-list/export')
@login_required
def report_employees_list_export():
    denied = _report_employees_access_denied()
    if denied:
        return denied
    department_id = (request.args.get('department_id') or '').strip()
    appointment = (request.args.get('appointment') or 'all').strip()
    fmt = (request.args.get('format') or 'csv').strip().lower()
    q = _report_employees_base_query()
    q = _report_employees_apply_filters(q, department_id, appointment)
    employees = q.order_by(Employee.last_name, Employee.first_name).all()
    rows = [_report_employees_row_dict(e) for e in employees]
    dept_by_id = {d.id: d for d in Department.query.all()}
    filter_label = _report_employees_filter_label(department_id, appointment, dept_by_id)
    base = f'employees_list_{date.today().strftime("%Y%m%d")}'

    if fmt == 'csv':
        data = _report_employees_csv_bytes(rows, filter_label)
        return Response(
            data,
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename={base}.csv'}
        )
    if fmt == 'xlsx':
        buf = _report_employees_xlsx_bytes(rows)
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'{base}.xlsx'
        )
    if fmt == 'pdf':
        buf = _report_employees_pdf_bytes(rows, filter_label)
        return send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'{base}.pdf'
        )
    flash('Invalid export format. Use csv, xlsx, or pdf.', 'error')
    return redirect(url_for('routes.report_employees_list', department_id=department_id, appointment=appointment))


def _parse_accrual_month_field(raw: str | None) -> int | None:
    """Accept 1–12 or full English month name (case-insensitive)."""
    if not raw:
        return None
    raw_stripped = raw.strip()
    try:
        n = int(raw_stripped)
        if 1 <= n <= 12:
            return n
    except ValueError:
        pass
    for fmt in ('%B', '%b'):
        try:
            return datetime.strptime(raw_stripped.title(), fmt).month
        except ValueError:
            continue
    return None


def _leave_accrual_target_period(year_raw: str | None, month_raw: str | None) -> tuple[int, int] | str:
    """
    Resolve year/month from Leave Credits accrual forms.

    Both blank: previous calendar month. Both filled: explicit period.
    Otherwise returns an error message string.
    """
    yr = (year_raw or '').strip()
    mo = (month_raw or '').strip()
    if yr and mo:
        try:
            y = int(yr)
            m = _parse_accrual_month_field(mo)
            if m is None:
                return 'Month must be a number 1–12 or a full month name (e.g. January).'
            return (y, m)
        except ValueError:
            return 'Invalid year or month.'
    if not yr and not mo:
        today = date.today()
        if today.month == 1:
            return (today.year - 1, 12)
        return (today.year, today.month - 1)
    return 'Provide both year and month, or leave both blank for the previous calendar month.'


@bp.route('/leave-credits')
@login_required
def leave_credits_list():
    """List employees eligible for leave credits (plantilla-style statuses; see LEAVE_CREDITS_STATUSES)."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employees = (Employee.query
                 .filter(Employee.status_of_appointment.in_(LEAVE_CREDITS_STATUSES))
                 .order_by(Employee.last_name, Employee.first_name)
                 .all())
    employee = current_user.employee if current_user.employee else None
    today = date.today()
    month_choices = [(m, calendar.month_name[m]) for m in range(1, 13)]
    return render_template(
        'leave_credits/list.html',
        employees=employees,
        employee=employee,
        accrual_year_default=today.year,
        accrual_month_default=today.month,
        month_choices=month_choices,
    )


@bp.route('/leave-credits/workweek', methods=['GET', 'POST'])
@login_required
def leave_credits_workweek():
    """Year / month / week calendar. Admin and HR only (managers and employees cannot open or POST)."""
    denied = _require_admin_or_hr()
    if denied:
        return denied

    if request.method == 'POST':
        try:
            year = int((request.form.get('year') or '').strip())
        except ValueError:
            year = date.today().year
        if year < 2000 or year > 2100:
            flash('Year must be between 2000 and 2100.', 'error')
            return redirect(url_for('routes.leave_credits_workweek'))
        try:
            result = save_year_calendar(year, request.form, user_id=current_user.id)
            db.session.commit()
            flash(
                f"Workweek calendar saved for {year}. "
                f"{result['weeks_saved']} week(s) stored. "
                f"Leave lines updated: {result['ledger_rows']} "
                f"({result['employees']} employee(s)).",
                'success',
            )
        except PermissionError:
            flash('Access denied. This section is for Admin and HR only.', 'error')
            return redirect(url_for(_dashboard_for_user(current_user)))
        except Exception as e:
            db.session.rollback()
            flash(f'Could not save workweek calendar: {e}', 'error')
        return redirect(url_for('routes.leave_credits_workweek', year=year))

    try:
        year = int((request.args.get('year') or '').strip() or date.today().year)
    except ValueError:
        year = date.today().year
    if year < 2000 or year > 2100:
        year = date.today().year
    view = build_year_view(year)
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'leave_credits/workweek.html',
        employee=employee,
        view=view,
        year=year,
        prev_year=year - 1,
        next_year=year + 1,
    )


@bp.route('/leave-credits/run-accrual', methods=['POST'])
@login_required
def leave_credits_run_accrual():
    """Post monthly VL/SL accrual for a calendar month (Admin/HR)."""
    denied = _require_admin_or_hr()
    if denied:
        return denied

    target = _leave_accrual_target_period(request.form.get('accrual_year'), request.form.get('accrual_month'))
    if isinstance(target, str):
        flash(target, 'error')
        return redirect(url_for('routes.leave_credits_list'))
    y, m = target

    try:
        result = accrue_monthly_vl_sl_for_month(y, m, created_by_user_id=current_user.id)
        msg = (
            f"Monthly accrual for {y}-{m:02d}: {result['created']} VL/SL row(s) created, "
            f"{result['skipped_duplicate']} VL/SL duplicate(s), "
            f"{result['skipped_not_employed']} not employed that month, "
            f"{result.get('skipped_separated', 0)} separated (skipped)."
        )
        if m == 1:
            msg += (
                f" Jan 1 annual SPL/WL: {result.get('created_annual_spl_wl', 0)} created, "
                f"{result.get('skipped_annual_duplicate', 0)} duplicate(s)."
            )
        if m == 12:
            msg += (
                f" Dec 31 SPL/WL lapse: {result.get('created_year_end_lapse', 0)} posted, "
                f"{result.get('skipped_lapse_duplicate', 0)} lapse duplicate(s), "
                f"{result.get('skipped_lapse_no_balance', 0)} with nothing to lapse."
            )
        flash(msg, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Accrual failed: {str(e)}', 'error')

    return redirect(url_for('routes.leave_credits_list'))


@bp.route('/leave-credits/undo-accrual', methods=['POST'])
@login_required
def leave_credits_undo_accrual():
    """Remove automated monthly accrual ledger rows for a calendar month (all employees)."""
    denied = _require_admin_or_hr()
    if denied:
        return denied

    target = _leave_accrual_target_period(
        request.form.get('undo_accrual_year'),
        request.form.get('undo_accrual_month'),
    )
    if isinstance(target, str):
        flash(target, 'error')
        return redirect(url_for('routes.leave_credits_list'))
    y, m = target

    try:
        result = undo_monthly_accrual_for_month(
            y,
            m,
            deleted_by_user_id=current_user.id,
            deleted_by_username=getattr(current_user, 'username', None),
        )
        nvl = result['deleted_vl_sl']
        nan = result['deleted_annual_spl_wl']
        nlp = result['deleted_year_end_lapse']
        nemp = result['employees_affected']
        total_rows = nvl + nan + nlp
        if total_rows == 0:
            flash(
                f'Nothing to undo for {y}-{m:02d}: no automated accrual rows matched '
                '(VL/SL monthly, Jan SPL/WL, or Dec lapse).',
                'info',
            )
        else:
            msg = (
                f'Undo accrual for {y}-{m:02d}: removed {total_rows} ledger row(s) '
                f'across {nemp} employee(s) '
                f'(VL/SL: {nvl}'
            )
            if m == 1:
                msg += f'; Jan SPL/WL: {nan}'
            if m == 12:
                msg += f'; Dec lapse: {nlp}'
            msg += '). Running balances were recomputed.'
            flash(msg, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Undo accrual failed: {str(e)}', 'error')

    return redirect(url_for('routes.leave_credits_list'))


def _leave_credits_card_context(employee_id: int):
    emp = (
        Employee.query
        .options(db.joinedload(Employee.department))
        .filter_by(id=employee_id)
        .first()
    )
    if not emp:
        abort(404)
    ledger_entries = (
        LeaveLedger.query
        .filter_by(employee_id=emp.id)
        .order_by(LeaveLedger.transaction_date.desc(), LeaveLedger.id.desc())
        .all()
    )
    from app.lwop_ledger_service import get_lwop_balance
    lwop_balance = get_lwop_balance(emp.id)
    return emp, ledger_entries, lwop_balance


def _leave_dec_str(val, places: int = 3) -> str:
    if val is None:
        return ''
    try:
        d = Decimal(str(val))
    except Exception:
        return ''
    if d == 0:
        return ''
    fmt = f'{{:,.{places}f}}'
    return fmt.format(d)


def _leave_credits_pdf_bytes(
    emp: Employee,
    ledger_entries: list[LeaveLedger],
    lwop_balance,
) -> io.BytesIO:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    page_w, page_h = landscape(A4)
    margin = 24
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    # Same municipal heading style as Service Record PDF.
    top_y = page_h - margin
    header_h = 48
    side_col_w = 64
    logo_size = 44
    header_bottom = top_y - header_h
    header_center_y = header_bottom + header_h / 2

    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'jagna_logo.png')
    left_col_center = margin + side_col_w / 2
    if os.path.exists(logo_path):
        logo_x = left_col_center - logo_size / 2
        logo_y = header_bottom + (header_h - logo_size) / 2
        c.drawImage(
            logo_path, logo_x, logo_y,
            width=logo_size, height=logo_size,
            preserveAspectRatio=True, mask='auto',
        )

    center_x = page_w / 2
    title_size, sub_size, line_gap = 14, 9.5, 0
    half_leading = (title_size + line_gap) / 2
    title_baseline = header_center_y + half_leading
    sub_baseline = header_center_y - half_leading
    c.setFont('Helvetica-Bold', title_size)
    c.drawCentredString(center_x, title_baseline, 'Municipality of Jagna')
    c.setFont('Helvetica', sub_size)
    c.drawCentredString(center_x, sub_baseline, 'Poblacion, Jagna, Bohol')

    sep_y = header_bottom - 8
    c.setStrokeColor(colors.HexColor('#0b3b6f'))
    c.setLineWidth(0.8)
    c.line(margin, sep_y, page_w - margin, sep_y)
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)

    c.setFont('Helvetica-Bold', 12)
    c.drawCentredString(center_x, sep_y - 16, 'EMPLOYEE LEAVE CREDITS')

    name = emp.directory_name
    if emp.middle_name:
        name = f'{name} {emp.middle_name}'
    c.setFont('Helvetica', 8)
    one_liner = (
        f'Emp ID: {emp.employee_id or "—"}'
        f'   |   Dept: {emp.department.name if emp.department else "—"}'
        f'   |   Name: {name}'
        f'   |   Position: {emp.position or "—"}'
    )
    c.drawString(margin, sep_y - 34, one_liner)

    lwop_txt = _leave_dec_str(lwop_balance) or '0.000'
    c.setFont('Helvetica', 7.5)
    c.drawString(margin, sep_y - 48, f'Current LWOP balance: {lwop_txt} day(s)')

    headers = [
        'Date', 'Particulars',
        'VL Ern', 'VL App', 'VL Bal',
        'SL Ern', 'SL App', 'SL Bal',
        'SPL Bal', 'WL Bal', 'CTO Bal', 'LWOP Bal',
        'Remarks',
    ]
    usable = page_w - (2 * margin)
    col_widths = [52, 130, 36, 36, 40, 36, 36, 40, 40, 40, 40, 44, usable - 570]
    if col_widths[-1] < 80:
        col_widths[-1] = 80
    table_x = margin
    table_y = sep_y - 62
    row_h = 16

    def draw_header(y_top: float):
        c.setFillColor(colors.HexColor('#0b3b6f'))
        c.rect(table_x, y_top - row_h, sum(col_widths), row_h, fill=1, stroke=1)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 6.4)
        x = table_x
        for idx, head in enumerate(headers):
            c.drawString(x + 2, y_top - 11, head)
            x += col_widths[idx]
        c.setFillColor(colors.black)
        c.setFont('Helvetica', 6.2)

    draw_header(table_y)
    y = table_y - row_h
    if not ledger_entries:
        y -= row_h
        c.rect(table_x, y, sum(col_widths), row_h, fill=0, stroke=1)
        c.drawString(table_x + 3, y + 5, 'No leave ledger entries have been recorded.')
    else:
        # Print oldest-first (date asc) to match the expected report ordering.
        ledger_entries_sorted = sorted(
            ledger_entries,
            key=lambda e: ((e.transaction_date or date.min), (e.id or 0)),
        )
        for entry in ledger_entries_sorted:
            y -= row_h
            if y < 70:
                c.showPage()
                y = page_h - margin - 24
                draw_header(y)
                y -= row_h

            vals = [
                entry.transaction_date.strftime('%Y-%m-%d') if entry.transaction_date else '—',
                entry.particulars or '—',
                _leave_dec_str(entry.vl_earned),
                _leave_dec_str(entry.vl_applied),
                _leave_dec_str(entry.vl_balance) or '0.000',
                _leave_dec_str(entry.sl_earned),
                _leave_dec_str(entry.sl_applied),
                _leave_dec_str(entry.sl_balance) or '0.000',
                _leave_dec_str(entry.spl_balance),
                _leave_dec_str(entry.wl_balance),
                _leave_dec_str(entry.cto_balance),
                _leave_dec_str(entry.lwop_balance),
                entry.remarks or '',
            ]
            x = table_x
            for idx, v in enumerate(vals):
                c.rect(x, y, col_widths[idx], row_h, fill=0, stroke=1)
                raw = str(v)
                max_chars = max(4, int(col_widths[idx] / 4.2))
                if len(raw) > max_chars:
                    raw = raw[: max_chars - 3] + '...'
                c.drawString(x + 2, y + 5, raw)
                x += col_widths[idx]

    sign_y = 44
    c.setFont('Helvetica', 9)
    c.line(page_w - 260, sign_y + 12, page_w - 70, sign_y + 12)
    c.drawCentredString(page_w - 165, sign_y - 2, 'HRMDO Head')
    c.drawCentredString(page_w - 165, sign_y - 14, 'Signature over Printed Name')

    c.save()
    buf.seek(0)
    return buf


@bp.route('/leave-credits/<int:id>')
@login_required
def leave_credits_card(id):
    """Leave card for a single employee: show leave ledger transactions."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp, ledger_entries, lwop_balance = _leave_credits_card_context(id)
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'leave_credits/card.html',
        emp=emp,
        ledger_entries=ledger_entries,
        employee=employee,
        lwop_balance=lwop_balance,
        can_export_pdf=True,
        pdf_url=url_for('routes.leave_credits_card_pdf', id=emp.id),
    )


@bp.route('/leave-credits/<int:id>/pdf')
@login_required
def leave_credits_card_pdf(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp, ledger_entries, lwop_balance = _leave_credits_card_context(id)
    buf = _leave_credits_pdf_bytes(emp, ledger_entries, lwop_balance)
    fname = f'leave_credits_{(emp.employee_id or str(emp.id)).strip()}.pdf'
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=fname)


@bp.route('/leave-credits/<int:id>/add', methods=['POST'])
@login_required
def leave_ledger_add(id):
    """Add or edit a leave ledger entry for an employee and recompute running balances.

    Add mode accepts one or more dates (comma-separated ``selected_dates``); each
    date creates its own ledger row. Filled usage fields are charged as one leave
    day per date: VL/SL/LWOP use the workweek setting (1.000 on 5-day, 1.250 on
    4-day); other leave kinds always use 1.000.
    Edit mode updates a single existing row using form amounts as entered.
    """
    denied = _require_admin_or_hr()
    if denied:
        return denied

    emp = Employee.query.get_or_404(id)

    try:
        entry_id = (request.form.get('entry_id') or '').strip()
        selected_dates_raw = (request.form.get('selected_dates') or '').strip()
        # Backward compatible with older single-date form field
        if not selected_dates_raw:
            selected_dates_raw = (request.form.get('transaction_date') or '').strip()
        particulars = (request.form.get('particulars') or '').strip()
        remarks = (request.form.get('remarks') or '').strip() or None

        selected_dates = []
        if selected_dates_raw:
            for part in selected_dates_raw.split(','):
                part = part.strip()
                if not part:
                    continue
                try:
                    selected_dates.append(datetime.strptime(part, '%Y-%m-%d').date())
                except ValueError as exc:
                    raise ValueError(f'Invalid date: {part}') from exc

        selected_dates = sorted(set(selected_dates))

        if not selected_dates or not particulars:
            flash('Date and Particulars are required.', 'error')
            return redirect(url_for('routes.leave_credits_card', id=id))

        def get_decimal(name):
            val = (request.form.get(name) or '').strip()
            return Decimal(val) if val else Decimal('0')

        # Collect all delta fields from the form
        delta_fields = [
            'vl_earned', 'vl_applied', 'vl_tardiness', 'vl_undertime',
            'sl_earned', 'sl_applied',
            'spl_earned', 'spl_used',
            'wl_earned', 'wl_used',
            'ml_credits', 'ml_used',
            'pl_credits', 'pl_used',
            'sp_credits', 'sp_used',
            'avaw_credits', 'avaw_used',
            'study_credits', 'study_used',
            'rehab_credits', 'rehab_used',
            'slbw_credits', 'slbw_used',
            'se_calamity_credits', 'se_calamity_used',
            'adopt_credits', 'adopt_used',
            'cto_earned', 'cto_used',
            'lwop_days',
        ]
        # Usage fields charged as one leave day per selected date on add.
        # VL / SL / LWOP follow workweek (1.000 or 1.250); other leave kinds = 1.000.
        # Earned/credits and tardiness/undertime keep the form values as entered.
        workweek_usage_fields = {
            'vl_applied', 'sl_applied', 'lwop_days',
        }
        flat_day_usage_fields = {
            'spl_used', 'wl_used',
            'ml_used', 'pl_used', 'sp_used', 'avaw_used',
            'study_used', 'rehab_used', 'slbw_used',
            'se_calamity_used', 'adopt_used',
            'cto_used',
        }
        deltas = {name: get_decimal(name) for name in delta_fields}
        active_workweek_usage = {
            name for name in workweek_usage_fields
            if deltas.get(name, Decimal('0')) > Decimal('0')
        }
        active_flat_day_usage = {
            name for name in flat_day_usage_fields
            if deltas.get(name, Decimal('0')) > Decimal('0')
        }

        def deltas_for_date(transaction_date):
            """Copy form deltas; override usage fields to one leave-day charge."""
            out = dict(deltas)
            if active_workweek_usage:
                day_charge = leave_day_credit_for_workweek(
                    _arrangement_model_code_for_date(emp, transaction_date)
                )
                for name in active_workweek_usage:
                    out[name] = day_charge
            if active_flat_day_usage:
                for name in active_flat_day_usage:
                    out[name] = Decimal('1.000')
            return out

        if entry_id:
            if len(selected_dates) != 1:
                flash('Editing an entry requires exactly one date.', 'error')
                return redirect(url_for('routes.leave_credits_card', id=id))
            transaction_date = selected_dates[0]
            target = LeaveLedger.query.filter_by(id=int(entry_id), employee_id=emp.id).first()
            if not target:
                flash('Ledger entry not found.', 'error')
                return redirect(url_for('routes.leave_credits_card', id=id))
            target.transaction_date = transaction_date
            target.particulars = particulars
            target.remarks = remarks
            # Edit keeps amounts as entered (allows half-day / corrections).
            for k, v in deltas.items():
                setattr(target, k, v)
            flash('Leave ledger entry saved successfully.', 'success')
        else:
            for transaction_date in selected_dates:
                row_deltas = deltas_for_date(transaction_date)
                target = LeaveLedger(
                    employee_id=emp.id,
                    transaction_date=transaction_date,
                    particulars=particulars,
                    remarks=remarks,
                    created_by=current_user.id,
                )
                for k, v in row_deltas.items():
                    setattr(target, k, v)
                db.session.add(target)
            db.session.flush()
            n = len(selected_dates)
            flash(
                f'{n} leave ledger {"entry" if n == 1 else "entries"} saved successfully.',
                'success',
            )

        recompute_leave_ledger_balances(emp.id)

        db.session.commit()

    except ValueError as e:
        flash(f'Invalid input: {str(e)}', 'error')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving entry: {str(e)}', 'error')

    return redirect(url_for('routes.leave_credits_card', id=id))


@bp.route('/leave-credits/<int:emp_id>/delete/<int:entry_id>', methods=['POST'])
@login_required
def leave_ledger_delete(emp_id, entry_id):
    """Delete a leave ledger entry and recompute running balances."""
    denied = _require_admin_or_hr()
    if denied:
        return denied

    emp = Employee.query.get_or_404(emp_id)
    try:
        entry = LeaveLedger.query.filter_by(id=entry_id, employee_id=emp.id).first()
        if not entry:
            flash('Ledger entry not found.', 'error')
            return redirect(url_for('routes.leave_credits_card', id=emp_id))

        record_leave_ledger_deletion(
            entry,
            deleted_by_user_id=current_user.id,
            deleted_by_username=getattr(current_user, 'username', None),
            source='leave_credits',
        )
        db.session.delete(entry)
        db.session.flush()

        recompute_leave_ledger_balances(emp.id)

        db.session.commit()
        flash('Leave ledger entry deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting entry: {str(e)}', 'error')

    return redirect(url_for('routes.leave_credits_card', id=emp_id))


@bp.route('/leave/apply', methods=['GET', 'POST'])
@login_required
def leave_apply():
    """Apply for leave (all roles). Creates a pending LeaveRequest."""
    emp = current_user.employee
    if not emp:
        flash('No linked employee record found for your account.', 'error')
        return redirect(url_for('routes.dashboard'))

    balances = _get_leave_balances_for_employee(emp)
    credit_groups = _leave_apply_credit_groups(emp, balances)
    credit_month_label = date.today().strftime('%B %Y')
    hidden_codes = _apply_leave_hidden_codes(emp)

    def _available_leave_type_options() -> list[tuple[str, str]]:
        opts = []
        for code, label in LEAVE_TYPE_OPTIONS:
            c = (code or '').strip().upper()
            if c in hidden_codes:
                continue
            key = _leave_code_to_balance_key(c)
            amt = balances.get(key, Decimal('0'))
            try:
                amt = Decimal(str(amt))
            except Exception:
                amt = Decimal('0')
            if amt > Decimal('0'):
                opts.append((code, label))
        return opts

    available_leave_type_options = _available_leave_type_options()

    if request.method == 'POST':
        leave_code = (request.form.get('leave_type') or '').strip().upper()
        reason = (request.form.get('reason') or '').strip()
        selected_dates_raw = (request.form.get('selected_dates') or '').strip()
        commutation = (request.form.get('commutation') or '').strip()
        extra_details = (request.form.get('extra_details') or '').strip()

        if not leave_code:
            flash('Please select a type of leave.', 'error')
            return redirect(url_for('routes.leave_apply'))

        selected_dates = []
        if selected_dates_raw:
            for part in selected_dates_raw.split(','):
                part = part.strip()
                if not part:
                    continue
                try:
                    selected_dates.append(datetime.strptime(part, '%Y-%m-%d').date())
                except ValueError:
                    pass

        if not selected_dates:
            flash('Please select at least one date to apply for.', 'error')
            return redirect(url_for('routes.leave_apply'))

        selected_dates = sorted(set(selected_dates))
        start_date = selected_dates[0]
        end_date = selected_dates[-1]
        total_days = Decimal(str(len(selected_dates)))

        # Wellness Leave: max 3 consecutive days per application
        if leave_code == 'WL':
            # check max consecutive streak
            streak = 1
            max_streak = 1
            for i in range(1, len(selected_dates)):
                if selected_dates[i] == selected_dates[i - 1] + timedelta(days=1):
                    streak += 1
                else:
                    max_streak = max(max_streak, streak)
                    streak = 1
            max_streak = max(max_streak, streak)
            if max_streak > 3:
                flash('Wellness Leave allows a maximum of 3 consecutive days per application.', 'error')
                return redirect(url_for('routes.leave_apply'))

        # Store extra details appended to reason for now (keeps schema unchanged)
        full_reason = reason

        # Special routing: If applicant is MO department OR user role is manager/admin,
        # route approvals to MO dept head + HRMDO/MADO dept head (in addition to normal manager queue).
        _dept_name = (emp.department.name if emp and emp.department and emp.department.name else '').strip().lower()
        _is_mo_dept = (_dept_name in ('mo', "mayor's office", 'mayors office', 'municipal mayor') or 'mayor' in _dept_name)
        _role = (getattr(current_user, 'role', None) or '').strip().lower()
        _special_route = _is_mo_dept or (_role in ('manager', 'admin'))
        if _special_route:
            full_reason = (full_reason + "\n__META__SPECIAL_APPROVAL=1").strip()

        if leave_code == 'SL':
            sl_case = (request.form.get('sl_case') or '').strip().upper()
            if sl_case not in ('IN_HOSPITAL', 'OUT_PATIENT'):
                flash('For Sick Leave, please select In-Hospital or Out-Patient.', 'error')
                return redirect(url_for('routes.leave_apply'))
            sl_illness = (request.form.get('sl_illness') or '').strip()
            case_line = (
                'In case of sick leave: In-Hospital'
                if sl_case == 'IN_HOSPITAL'
                else 'In case of sick leave: Out-Patient'
            )
            full_reason = (full_reason + '\n\n' + case_line).strip()
            full_reason = (full_reason + f"\n__META__SL_CASE={sl_case}").strip()
            if sl_illness:
                full_reason = (full_reason + f"\n__META__SL_ILLNESS={sl_illness}").strip()

        if leave_code in ('VL', 'SPL'):
            loc = (request.form.get('vl_location') or '').strip().upper()
            abroad_place = (request.form.get('vl_abroad_place') or '').strip()
            if loc == 'WITHIN_PH':
                full_reason = (full_reason + "\n__META__VL_LOCATION=WITHIN_PH").strip()
            elif loc == 'ABROAD':
                if not abroad_place:
                    flash('For VL/SPL Abroad, please specify the place.', 'error')
                    return redirect(url_for('routes.leave_apply'))
                full_reason = (full_reason + f"\n__META__VL_LOCATION=ABROAD:{abroad_place}").strip()

        if extra_details:
            full_reason = (full_reason + "\n\n" + extra_details).strip()
        if commutation:
            full_reason = (full_reason + f"\n\nCommutation: {commutation}").strip()

        try:
            lr = LeaveRequest(
                employee_id=emp.id,
                leave_type=leave_code,
                start_date=start_date,
                end_date=end_date,
                total_days=total_days,
                reason=full_reason,
                status='pending',
                created_at=datetime.utcnow(),
            )
            db.session.add(lr)
            db.session.commit()
            if _special_route:
                flash('Leave application submitted. Notified MO and HRMDO/MADO department heads for approval/disapproval.', 'success')
            else:
                flash('Leave application submitted. Waiting for Manager approval.', 'success')
            return redirect(url_for('routes.leave_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error submitting leave: {str(e)}', 'error')
            return redirect(url_for('routes.leave_apply'))

    return render_template('leave_online/apply.html',
                           employee=emp,
                           balances=balances,
                           credit_groups=credit_groups,
                           credit_month_label=credit_month_label,
                           leave_type_options=available_leave_type_options)


@bp.route('/leave')
@login_required
def leave_list():
    """View leave applications (all roles). Employee sees own; Admin/HR/Manager sees all."""
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    emp = current_user.employee
    q = LeaveRequest.query
    if role == 'manager':
        dept_ids = _managed_department_ids_for_manager()
        if not dept_ids:
            flash('No managed department is linked to your account.', 'error')
            leaves = []
            employee = current_user.employee if current_user.employee else None
            return render_template('leave_online/list.html', leaves=leaves, employee=employee)
        q = (q.join(Employee, Employee.id == LeaveRequest.employee_id)
               .filter(Employee.department_id.in_(dept_ids)))
    elif role not in ('admin', 'hr', 'manager'):
        if not emp:
            flash('No linked employee record found for your account.', 'error')
            return redirect(url_for('routes.dashboard'))
        q = q.filter(LeaveRequest.employee_id == emp.id)
    leaves = q.order_by(LeaveRequest.created_at.desc()).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('leave_online/list.html', leaves=leaves, employee=employee)


@bp.route('/leave/<int:id>')
@login_required
def leave_detail(id):
    """View a leave application detail; print enabled if approved."""
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    emp = current_user.employee
    lr = LeaveRequest.query.get_or_404(id)
    if role not in ('admin', 'hr', 'manager'):
        if not emp or lr.employee_id != emp.id:
            flash('Access denied.', 'error')
            return redirect(url_for('routes.leave_list'))
    employee = current_user.employee if current_user.employee else None
    target_emp = Employee.query.get(lr.employee_id) if lr.employee_id else None
    applicant_is_manager = False
    applicant_is_department_manager = False
    if target_emp and getattr(target_emp, 'user_id', None):
        ap_user = User.query.get(target_emp.user_id)
        if ap_user and (getattr(ap_user, 'role', None) or '').strip().lower() == 'manager':
            applicant_is_manager = True
    if target_emp:
        # True when this employee is assigned as department manager in Departments table.
        applicant_is_department_manager = Department.query.filter_by(manager_id=target_emp.id).first() is not None
    salary_amount = _current_salary_amount(target_emp) if target_emp else None
    balances = _get_leave_balances_for_employee(target_emp) if target_emp else {}

    # HR signatory (HRMDO department head / manager)
    hr_signatory = None
    try:
        from sqlalchemy import func as sql_func
        hr_dept = (Department.query
                   .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                   .first())
        if not hr_dept:
            hr_dept = (Department.query
                       .filter(sql_func.lower(Department.name).like('%hr%'))
                       .first())
        hr_signatory = hr_dept.manager if hr_dept and hr_dept.manager else None
    except Exception:
        hr_signatory = None

    # Mayor signatory (MO / Mayor's Office department head / manager)
    mayor_signatory = None
    try:
        from sqlalchemy import func as sql_func
        mo_dept = (Department.query
                   .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                   .first())
        if not mo_dept:
            mo_dept = (Department.query
                       .filter(sql_func.lower(Department.name).like('%mayor%'))
                       .first())
        if not mo_dept:
            mo_dept = (Department.query
                       .filter(sql_func.lower(Department.name).like('%mo%'))
                       .first())
        mayor_signatory = mo_dept.manager if mo_dept and mo_dept.manager else None
    except Exception:
        mayor_signatory = None

    # MADO signatory (Municipal Administrator's Office department head / manager)
    mado_signatory = None
    try:
        from sqlalchemy import func as sql_func
        # Prefer departments that actually have a manager assigned
        mado_dept = (Department.query
                     .filter(sql_func.lower(Department.name).like('%mado%'))
                     .filter(Department.manager_id.isnot(None))
                     .first())
        if not mado_dept:
            mado_dept = (Department.query
                         .filter(sql_func.lower(Department.name).like('%administrator%'))
                         .filter(Department.manager_id.isnot(None))
                         .first())
        # Fallback: any MADO/administrator-like department (even if manager_id is null)
        if not mado_dept:
            mado_dept = (Department.query
                         .filter(sql_func.lower(Department.name).like('%mado%'))
                         .first())
        if not mado_dept:
            mado_dept = (Department.query
                         .filter(sql_func.lower(Department.name).like('%administrator%'))
                         .first())
        mado_signatory = mado_dept.manager if mado_dept and mado_dept.manager else None
    except Exception:
        mado_signatory = None

    return render_template(
        'leave_online/detail.html',
        leave=lr,
        employee=employee,
        target_emp=target_emp,
        salary_amount=salary_amount,
        balances=balances,
        hr_signatory=hr_signatory,
        mayor_signatory=mayor_signatory,
        mado_signatory=mado_signatory,
        applicant_is_manager=applicant_is_manager,
        applicant_is_department_manager=applicant_is_department_manager,
    )


@bp.route('/leave-approvals')
@login_required
def leave_approvals():
    """Manager approvals list (pending leave requests)."""
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('manager', 'admin'):
        flash('Access denied. This page is for Managers/Admin only.', 'error')
        return redirect(url_for('routes.dashboard'))

    def _is_special_approver_manager() -> bool:
        """True if current manager is MO or HRMDO/HRMO or MADO department head."""
        emp = current_user.employee
        if not emp:
            return False
        try:
            from sqlalchemy import func as sql_func
            # MO / Mayor's Office
            mo_dept = (Department.query
                       .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                       .first())
            if not mo_dept:
                mo_dept = Department.query.filter(sql_func.lower(Department.name).like('%mayor%')).first()
            # HRMDO / HRMO
            hrmdo_dept = (Department.query
                          .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                          .first())
            if not hrmdo_dept:
                hrmdo_dept = Department.query.filter(sql_func.lower(Department.name).like('%hrmo%')).first()
            # MADO (separate; do NOT fallback-chain behind HRMDO)
            mado_dept = Department.query.filter(sql_func.lower(Department.name).like('%mado%')).first()
            return (
                (mo_dept and mo_dept.manager_id == emp.id) or
                (hrmdo_dept and hrmdo_dept.manager_id == emp.id) or
                (mado_dept and mado_dept.manager_id == emp.id)
            )
        except Exception:
            return False

    def _is_special_approver_admin() -> bool:
        """True if current admin is MO or HRMDO/HRMO or MADO department head."""
        emp = current_user.employee
        if not emp:
            return False
        try:
            from sqlalchemy import func as sql_func
            mo_dept = (Department.query
                       .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                       .first())
            if not mo_dept:
                mo_dept = Department.query.filter(sql_func.lower(Department.name).like('%mayor%')).first()
            hrmdo_dept = (Department.query
                          .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                          .first())
            if not hrmdo_dept:
                hrmdo_dept = Department.query.filter(sql_func.lower(Department.name).like('%hrmo%')).first()
            mado_dept = Department.query.filter(sql_func.lower(Department.name).like('%mado%')).first()
            return (
                (mo_dept and mo_dept.manager_id == emp.id) or
                (hrmdo_dept and hrmdo_dept.manager_id == emp.id) or
                (mado_dept and mado_dept.manager_id == emp.id)
            )
        except Exception:
            return False

    dept_ids = _managed_department_ids_for_manager()
    pending = []
    if role == 'manager':
        if not dept_ids:
            flash('No managed department is linked to your account.', 'error')
        else:
            q = (LeaveRequest.query
                 .join(Employee, Employee.id == LeaveRequest.employee_id)
                 .filter(LeaveRequest.status == 'pending'))
            # Normal scope: managed departments
            q = q.filter(Employee.department_id.in_(dept_ids))
            pending = q.order_by(LeaveRequest.created_at.desc()).all()

    # Special approver scope: MO + HRMDO/HRMO + MADO dept heads see all special-routed leaves.
    if role == 'manager' and _is_special_approver_manager():
        try:
            from sqlalchemy import func as sql_func
            special = (
                LeaveRequest.query
                .join(Employee, Employee.id == LeaveRequest.employee_id)
                .outerjoin(Department, Department.id == Employee.department_id)
                .outerjoin(User, User.id == Employee.user_id)
                .filter(LeaveRequest.status == 'pending')
                .filter(
                    (LeaveRequest.reason.ilike('%__META__SPECIAL_APPROVAL=1%')) |
                    (sql_func.lower(User.role).in_(['manager', 'admin'])) |
                    (sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor'])) |
                    (sql_func.lower(Department.name).like('%mayor%'))
                )
                .order_by(LeaveRequest.created_at.desc())
                .all()
            )
            # Merge, keep unique by id
            by_id = {l.id: l for l in (pending or [])}
            for l in special:
                by_id[l.id] = l
            pending = list(by_id.values())
            pending.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
        except Exception:
            pass

    # Admin approvals list:
    # - If admin is a dept head (MO/HRMDO/HRMO/MADO), show special-routed pending leaves.
    # - Otherwise, show pending leaves filed by users with role Manager (special approver behavior for admin role).
    if role == 'admin':
        try:
            from sqlalchemy import func as sql_func
            if _is_special_approver_admin():
                pending = (LeaveRequest.query
                           .join(Employee, Employee.id == LeaveRequest.employee_id)
                           .outerjoin(Department, Department.id == Employee.department_id)
                           .outerjoin(User, User.id == Employee.user_id)
                           .filter(LeaveRequest.status == 'pending')
                           .filter(
                               (LeaveRequest.reason.ilike('%__META__SPECIAL_APPROVAL=1%')) |
                               (sql_func.lower(User.role).in_(['manager', 'admin'])) |
                               (sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor'])) |
                               (sql_func.lower(Department.name).like('%mayor%'))
                           )
                           .order_by(LeaveRequest.created_at.desc())
                           .all())
            else:
                pending = (LeaveRequest.query
                           .join(Employee, Employee.id == LeaveRequest.employee_id)
                           .outerjoin(User, User.id == Employee.user_id)
                           .filter(LeaveRequest.status == 'pending')
                           .filter(sql_func.lower(User.role) == 'manager')
                           .order_by(LeaveRequest.created_at.desc())
                           .all())
        except Exception:
            # If anything goes wrong, fail closed (show nothing)
            pending = []

    # Managers must never be able to approve/disapprove their own leave applications.
    # Manager-filed leaves are specially routed to MO + HRMDO/MADO department heads.
    try:
        if role == 'manager' and current_user.employee:
            pending = [l for l in (pending or []) if l.employee_id != current_user.employee.id]
    except Exception:
        pass
    employee = current_user.employee if current_user.employee else None
    return render_template('leave_online/approvals.html', leaves=pending, employee=employee)


@bp.route('/leave/<int:id>/approve', methods=['POST'])
@login_required
def leave_approve(id):
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('manager', 'admin'):
        flash('Access denied.', 'error')
        return redirect(url_for('routes.dashboard'))
    lr = LeaveRequest.query.get_or_404(id)
    if role == 'manager' and current_user.employee and lr.employee_id == current_user.employee.id:
        flash('Access denied. You cannot approve your own leave application.', 'error')
        return redirect(url_for('routes.leave_approvals'))
    if role == 'admin':
        # Admin can act either as:
        # - Dept head special approver (MO/HRMDO/HRMO/MADO) for special-routed leaves, OR
        # - Special approver for Manager-filed leaves (fallback).
        try:
            from sqlalchemy import func as sql_func
            emp = current_user.employee
            # dept head check
            mo_dept = (Department.query
                       .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                       .first()) or Department.query.filter(sql_func.lower(Department.name).like('%mayor%')).first()
            hrmdo_dept = (Department.query
                          .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                          .first()) or Department.query.filter(sql_func.lower(Department.name).like('%hrmo%')).first()
            mado_dept = Department.query.filter(sql_func.lower(Department.name).like('%mado%')).first()
            is_dept_head = bool(emp and (
                (mo_dept and mo_dept.manager_id == emp.id) or
                (hrmdo_dept and hrmdo_dept.manager_id == emp.id) or
                (mado_dept and mado_dept.manager_id == emp.id)
            ))

            reason = (lr.reason or '')
            applicant_role = ((lr.employee.user.role if lr.employee and lr.employee.user else '') or '').strip().lower()
            dept_name = ((lr.employee.department.name if lr.employee and lr.employee.department else '') or '').strip().lower()
            is_mo_dept = (dept_name in ('mo', "mayor's office", 'mayors office', 'municipal mayor') or 'mayor' in dept_name)
            is_special = ('__META__SPECIAL_APPROVAL=1' in reason) or (applicant_role in ('manager', 'admin')) or is_mo_dept

            if is_dept_head:
                if not is_special:
                    flash('Access denied.', 'error')
                    return redirect(url_for('routes.leave_approvals'))
            else:
                if applicant_role != 'manager':
                    flash('Access denied.', 'error')
                    return redirect(url_for('routes.leave_approvals'))
        except Exception:
            flash('Access denied.', 'error')
            return redirect(url_for('routes.leave_approvals'))
    dept_ids = _managed_department_ids_for_manager()
    emp = Employee.query.get(lr.employee_id) if lr.employee_id else None
    allowed = bool(dept_ids and emp and emp.department_id in dept_ids)
    if not allowed:
        # Allow MO / HRMDO/MADO department heads to act on specially routed leaves.
        try:
            reason = (lr.reason or '')
            is_special = '__META__SPECIAL_APPROVAL=1' in reason
            applicant_role = ((lr.employee.user.role if lr.employee and lr.employee.user else '') or '').strip().lower()
            dept_name = ((lr.employee.department.name if lr.employee and lr.employee.department else '') or '').strip().lower()
            is_mo_dept = (dept_name in ('mo', "mayor's office", 'mayors office', 'municipal mayor') or 'mayor' in dept_name)
            is_special = is_special or (applicant_role in ('manager', 'admin')) or is_mo_dept
            if is_special and current_user.employee:
                from sqlalchemy import func as sql_func
                mo_dept = (Department.query
                           .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                           .first())
                if not mo_dept:
                    mo_dept = Department.query.filter(sql_func.lower(Department.name).like('%mayor%')).first()
                hrmdo_dept = (Department.query
                              .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                              .first())
                if not hrmdo_dept:
                    hrmdo_dept = Department.query.filter(sql_func.lower(Department.name).like('%hrmo%')).first()
                if not hrmdo_dept:
                    hrmdo_dept = Department.query.filter(sql_func.lower(Department.name).like('%mado%')).first()
                allowed = (
                    (mo_dept and mo_dept.manager_id == current_user.employee.id) or
                    (hrmdo_dept and hrmdo_dept.manager_id == current_user.employee.id)
                )
        except Exception:
            allowed = False
    if not allowed:
        flash('Access denied. You can only approve leave for employees in your department.', 'error')
        return redirect(url_for('routes.leave_approvals'))
    remarks = (request.form.get('manager_remarks') or '').strip()
    try:
        # If already approved, don't double-post ledger deductions
        if (lr.status or '').strip().lower() == 'approved':
            flash('Leave application is already approved.', 'info')
            return redirect(url_for('routes.leave_approvals'))

        lr.status = 'approved'
        lr.approved_by = current_user.id
        lr.approved_at = datetime.utcnow()
        lr.rejection_reason = None
        if remarks:
            lr.reason = (lr.reason or '').strip() + f"\n\nManager remarks: {remarks}"

        # --- Post deduction to leave_ledger (idempotent via remarks tag) ---
        tag = f'leave_request_id={lr.id}'
        existing_post = LeaveLedger.query.filter_by(employee_id=lr.employee_id, remarks=tag).first()
        if not existing_post:
            leave_code = (lr.leave_type or '').strip().upper()
            key = _leave_code_to_balance_key(leave_code)
            col_by_key = {
                'vl': 'vl_applied',
                'sl': 'sl_applied',
                'spl': 'spl_used',
                'wl': 'wl_used',
                'cto': 'cto_used',
                'ml': 'ml_used',
                'pl': 'pl_used',
                'sp': 'sp_used',
                'avaw': 'avaw_used',
                'study': 'study_used',
                'rehab': 'rehab_used',
                'slbw': 'slbw_used',
                'se_calamity': 'se_calamity_used',
                'adopt': 'adopt_used',
            }
            target_col = col_by_key.get(key)
            # Fallback: if unknown leave code, do not post ledger row
            if target_col:
                try:
                    days = Decimal(str(lr.total_days)) if lr.total_days is not None else None
                except Exception:
                    days = None
                if days is None:
                    # inclusive day count
                    try:
                        days = Decimal(str((lr.end_date - lr.start_date).days + 1))
                    except Exception:
                        days = Decimal('1')

                particulars = (
                    f'Online Leave Approved - {leave_code} '
                    f'({lr.start_date.isoformat()} to {lr.end_date.isoformat()})'
                )
                row = LeaveLedger(
                    employee_id=lr.employee_id,
                    transaction_date=lr.start_date,
                    particulars=particulars,
                    remarks=tag,
                    created_by=current_user.id,
                )
                setattr(row, target_col, days)
                db.session.add(row)
                db.session.flush()
                recompute_leave_ledger_balances(lr.employee_id)

        db.session.commit()
        flash('Leave application approved.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error approving leave: {str(e)}', 'error')
    return redirect(url_for('routes.leave_approvals'))


@bp.route('/leave/<int:id>/disapprove', methods=['POST'])
@login_required
def leave_disapprove(id):
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('manager', 'admin'):
        flash('Access denied.', 'error')
        return redirect(url_for('routes.dashboard'))
    lr = LeaveRequest.query.get_or_404(id)
    if role == 'manager' and current_user.employee and lr.employee_id == current_user.employee.id:
        flash('Access denied. You cannot dis-approve your own leave application.', 'error')
        return redirect(url_for('routes.leave_approvals'))
    if role == 'admin':
        # Admin can act either as:
        # - Dept head special approver (MO/HRMDO/HRMO/MADO) for special-routed leaves, OR
        # - Special approver for Manager-filed leaves (fallback).
        try:
            from sqlalchemy import func as sql_func
            emp = current_user.employee
            mo_dept = (Department.query
                       .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                       .first()) or Department.query.filter(sql_func.lower(Department.name).like('%mayor%')).first()
            hrmdo_dept = (Department.query
                          .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                          .first()) or Department.query.filter(sql_func.lower(Department.name).like('%hrmo%')).first()
            mado_dept = Department.query.filter(sql_func.lower(Department.name).like('%mado%')).first()
            is_dept_head = bool(emp and (
                (mo_dept and mo_dept.manager_id == emp.id) or
                (hrmdo_dept and hrmdo_dept.manager_id == emp.id) or
                (mado_dept and mado_dept.manager_id == emp.id)
            ))

            reason = (lr.reason or '')
            applicant_role = ((lr.employee.user.role if lr.employee and lr.employee.user else '') or '').strip().lower()
            dept_name = ((lr.employee.department.name if lr.employee and lr.employee.department else '') or '').strip().lower()
            is_mo_dept = (dept_name in ('mo', "mayor's office", 'mayors office', 'municipal mayor') or 'mayor' in dept_name)
            is_special = ('__META__SPECIAL_APPROVAL=1' in reason) or (applicant_role in ('manager', 'admin')) or is_mo_dept

            if is_dept_head:
                if not is_special:
                    flash('Access denied.', 'error')
                    return redirect(url_for('routes.leave_approvals'))
            else:
                if applicant_role != 'manager':
                    flash('Access denied.', 'error')
                    return redirect(url_for('routes.leave_approvals'))
        except Exception:
            flash('Access denied.', 'error')
            return redirect(url_for('routes.leave_approvals'))
    dept_ids = _managed_department_ids_for_manager()
    emp = Employee.query.get(lr.employee_id) if lr.employee_id else None
    allowed = bool(dept_ids and emp and emp.department_id in dept_ids)
    if not allowed:
        # Allow MO / HRMDO/MADO department heads to act on specially routed leaves.
        try:
            reason = (lr.reason or '')
            is_special = '__META__SPECIAL_APPROVAL=1' in reason
            applicant_role = ((lr.employee.user.role if lr.employee and lr.employee.user else '') or '').strip().lower()
            dept_name = ((lr.employee.department.name if lr.employee and lr.employee.department else '') or '').strip().lower()
            is_mo_dept = (dept_name in ('mo', "mayor's office", 'mayors office', 'municipal mayor') or 'mayor' in dept_name)
            is_special = is_special or (applicant_role in ('manager', 'admin')) or is_mo_dept
            if is_special and current_user.employee:
                from sqlalchemy import func as sql_func
                mo_dept = (Department.query
                           .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                           .first())
                if not mo_dept:
                    mo_dept = Department.query.filter(sql_func.lower(Department.name).like('%mayor%')).first()
                hrmdo_dept = (Department.query
                              .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                              .first())
                if not hrmdo_dept:
                    hrmdo_dept = Department.query.filter(sql_func.lower(Department.name).like('%hrmo%')).first()
                if not hrmdo_dept:
                    hrmdo_dept = Department.query.filter(sql_func.lower(Department.name).like('%mado%')).first()
                allowed = (
                    (mo_dept and mo_dept.manager_id == current_user.employee.id) or
                    (hrmdo_dept and hrmdo_dept.manager_id == current_user.employee.id)
                )
        except Exception:
            allowed = False
    if not allowed:
        flash('Access denied. You can only dis-approve leave for employees in your department.', 'error')
        return redirect(url_for('routes.leave_approvals'))
    remarks = (request.form.get('manager_remarks') or '').strip()
    try:
        lr.status = 'rejected'
        lr.approved_by = current_user.id
        lr.approved_at = datetime.utcnow()
        lr.rejection_reason = remarks or 'Dis-approved'
        db.session.commit()
        flash('Leave application dis-approved.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error dis-approving leave: {str(e)}', 'error')
    return redirect(url_for('routes.leave_approvals'))


@bp.route('/dashboard')
@login_required
def dashboard():
    if (getattr(current_user, 'role', None) or '').strip().lower() in ('employee', 'dtr_uploader'):
        return redirect(url_for('routes.employee_dashboard'))
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    employee = current_user.employee if current_user.employee else None
    # Get statistics
    total_employees = Employee.query.filter_by(status='active').count()
    total_users = User.query.filter_by(is_active=True).count()
    pending_leaves = LeaveRequest.query.filter_by(status='pending').count()
    on_leave_today = LeaveRequest.query.filter(
        LeaveRequest.status == 'approved',
        LeaveRequest.start_date <= date.today(),
        LeaveRequest.end_date >= date.today()
    ).count()
    pending_approvals = None

    # Step increment due: last SI (or appointment) + 3 years; includes overdue + next 15 days
    step_increment_due_count = 0
    try:
        plantilla_emps = (
            Employee.query
            .filter(Employee.appointment_date.isnot(None))
            .filter(Employee.status.in_(('active', 'inactive')))
            .all()
        )
        step_increment_due_count = len(_step_increment_due_employees(plantilla_emps))
    except Exception:
        step_increment_due_count = 0
    
    # Get recent leave requests
    recent_leaves = LeaveRequest.query.order_by(LeaveRequest.created_at.desc()).limit(10).all()

    from app.employee_id_photo_service import employee_has_id_photo

    # Manager dashboard: scope leave stats + list to managed department(s)
    manager_leave_scope = None
    if role == 'manager':
        dept_ids = _managed_department_ids_for_manager()
        manager_leave_scope = dept_ids
        if dept_ids:
            pending_leaves = (LeaveRequest.query
                              .join(Employee, Employee.id == LeaveRequest.employee_id)
                              .filter(LeaveRequest.status == 'pending', Employee.department_id.in_(dept_ids))
                              .count())
            recent_leaves = (LeaveRequest.query
                             .join(Employee, Employee.id == LeaveRequest.employee_id)
                             .filter(Employee.department_id.in_(dept_ids))
                             .order_by(LeaveRequest.created_at.desc())
                             .limit(10)
                             .all())
            # Special approver scope: MO + HRMDO/HRMO/MADO dept heads also see specially routed leaves.
            try:
                if employee:
                    from sqlalchemy import func as sql_func
                    mo_dept = (Department.query
                               .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                               .first())
                    if not mo_dept:
                        mo_dept = Department.query.filter(sql_func.lower(Department.name).like('%mayor%')).first()
                    hrmdo_dept = (Department.query
                                  .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                                  .first())
                    if not hrmdo_dept:
                        hrmdo_dept = Department.query.filter(sql_func.lower(Department.name).like('%hrmo%')).first()
                    # MADO is a separate dept; don't hide it behind HRMDO fallback
                    mado_dept = Department.query.filter(sql_func.lower(Department.name).like('%mado%')).first()

                    is_special_approver = (
                        (mo_dept and mo_dept.manager_id == employee.id) or
                        (hrmdo_dept and hrmdo_dept.manager_id == employee.id) or
                        (mado_dept and mado_dept.manager_id == employee.id)
                    )
                    if is_special_approver:
                        # Backward-compatible special routing: meta tag OR applicant role manager/admin OR MO dept.
                        special_cond = (
                            (LeaveRequest.reason.ilike('%__META__SPECIAL_APPROVAL=1%')) |
                            (sql_func.lower(User.role).in_(['manager', 'admin'])) |
                            (sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor'])) |
                            (sql_func.lower(Department.name).like('%mayor%'))
                        )

                        special_pending = (LeaveRequest.query
                                           .join(Employee, Employee.id == LeaveRequest.employee_id)
                                           .outerjoin(Department, Department.id == Employee.department_id)
                                           .outerjoin(User, User.id == Employee.user_id)
                                           .filter(LeaveRequest.status == 'pending')
                                           .filter(special_cond)
                                           .order_by(LeaveRequest.created_at.desc())
                                           .limit(50)
                                           .all())
                        special_recent = (LeaveRequest.query
                                          .join(Employee, Employee.id == LeaveRequest.employee_id)
                                          .outerjoin(Department, Department.id == Employee.department_id)
                                          .outerjoin(User, User.id == Employee.user_id)
                                          .filter(special_cond)
                                          .order_by(LeaveRequest.created_at.desc())
                                          .limit(50)
                                          .all())

                        # Update pending approvals count (avoid double-count by id)
                        special_pending_ids = {l.id for l in special_pending}
                        base_pending_ids = {
                            r[0] for r in (LeaveRequest.query
                                           .with_entities(LeaveRequest.id)
                                           .join(Employee, Employee.id == LeaveRequest.employee_id)
                                           .filter(LeaveRequest.status == 'pending', Employee.department_id.in_(dept_ids))
                                           .all())
                        }
                        pending_leaves = len(base_pending_ids.union(special_pending_ids))

                        # Merge recent leaves list (dept + special), keep unique by id, then trim
                        by_id = {l.id: l for l in (recent_leaves or [])}
                        for l in special_recent:
                            by_id[l.id] = l
                        recent_leaves = list(by_id.values())
                        recent_leaves.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
                        recent_leaves = recent_leaves[:10]
            except Exception:
                pass
        else:
            pending_leaves = 0
            recent_leaves = []
        pending_approvals = pending_leaves

    # Admin dashboard: include department leaves + special-routed leaves.
    if role == 'admin':
        try:
            from sqlalchemy import func as sql_func

            # Dept-scope for admin if admin user is linked to an employee/department.
            dept_pending_ids = set()
            dept_recent = []
            if employee and employee.department_id:
                dept_pending_ids = {
                    r[0] for r in (LeaveRequest.query
                                   .with_entities(LeaveRequest.id)
                                   .join(Employee, Employee.id == LeaveRequest.employee_id)
                                   .filter(LeaveRequest.status == 'pending', Employee.department_id == employee.department_id)
                                   .all())
                }
                dept_recent = (LeaveRequest.query
                               .join(Employee, Employee.id == LeaveRequest.employee_id)
                               .filter(Employee.department_id == employee.department_id)
                               .order_by(LeaveRequest.created_at.desc())
                               .limit(50)
                               .all())

            # Special-routed leaves (for "recent" table)
            special_cond = (
                (LeaveRequest.reason.ilike('%__META__SPECIAL_APPROVAL=1%')) |
                (sql_func.lower(User.role).in_(['manager', 'admin'])) |
                (sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor'])) |
                (sql_func.lower(Department.name).like('%mayor%'))
            )
            special_recent = (LeaveRequest.query
                              .join(Employee, Employee.id == LeaveRequest.employee_id)
                              .outerjoin(Department, Department.id == Employee.department_id)
                              .outerjoin(User, User.id == Employee.user_id)
                              .filter(special_cond)
                              .order_by(LeaveRequest.created_at.desc())
                              .limit(50)
                              .all())

            # Determine if this Admin is actually a recognized dept head (MO/HRMDO/HRMO/MADO).
            emp_id = employee.id if employee else None
            mo_dept = (Department.query
                       .filter(sql_func.lower(Department.name).in_(['mo', "mayor's office", 'mayors office', 'municipal mayor']))
                       .first())
            if not mo_dept:
                mo_dept = Department.query.filter(sql_func.lower(Department.name).like('%mayor%')).first()
            hrmdo_dept = (Department.query
                          .filter(sql_func.lower(Department.name).like('%hrmdo%'))
                          .first())
            if not hrmdo_dept:
                hrmdo_dept = Department.query.filter(sql_func.lower(Department.name).like('%hrmo%')).first()
            mado_dept = Department.query.filter(sql_func.lower(Department.name).like('%mado%')).first()

            is_special_dept_head = bool(emp_id and (
                (mo_dept and mo_dept.manager_id == emp_id) or
                (hrmdo_dept and hrmdo_dept.manager_id == emp_id) or
                (mado_dept and mado_dept.manager_id == emp_id)
            ))

            if is_special_dept_head:
                # Dept heads should see/count pending special-routed leaves (includes admin-filed + manager-filed + MO),
                # PLUS their own department pending leaves.
                special_pending_ids = {
                    r[0] for r in (LeaveRequest.query
                                   .with_entities(LeaveRequest.id)
                                   .join(Employee, Employee.id == LeaveRequest.employee_id)
                                   .outerjoin(Department, Department.id == Employee.department_id)
                                   .outerjoin(User, User.id == Employee.user_id)
                                   .filter(LeaveRequest.status == 'pending')
                                   .filter(special_cond)
                                   .all())
                }
                pending_approvals = len(dept_pending_ids.union(special_pending_ids))
            else:
                # Fallback: Admin special approver counts pending leaves filed by users with role Manager,
                # plus department pending leaves (if any).
                special_pending_manager_ids = {
                    r[0] for r in (LeaveRequest.query
                                   .with_entities(LeaveRequest.id)
                                   .join(Employee, Employee.id == LeaveRequest.employee_id)
                                   .outerjoin(User, User.id == Employee.user_id)
                                   .filter(LeaveRequest.status == 'pending')
                                   .filter(sql_func.lower(User.role) == 'manager')
                                   .all())
                }
                pending_approvals = len(dept_pending_ids.union(special_pending_manager_ids))

            # Recent table: dept leaves + special-routed leaves (dedupe, then trim)
            by_id = {l.id: l for l in (dept_recent or [])}
            for l in special_recent:
                by_id[l.id] = l
            recent_leaves = list(by_id.values())
            recent_leaves.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
            recent_leaves = recent_leaves[:10]
        except Exception:
            pending_approvals = pending_approvals if pending_approvals is not None else 0
            # leave recent_leaves as-is if something fails
    
    return render_template('dashboard.html',
                         total_employees=total_employees,
                         total_users=total_users,
                         pending_leaves=pending_leaves,
                         pending_approvals=pending_approvals,
                         on_leave_today=on_leave_today,
                         step_increment_due_count=step_increment_due_count,
                         recent_leaves=recent_leaves,
                         manager_leave_scope=manager_leave_scope,
                         employee=employee,
                         has_id_photo=employee_has_id_photo(employee) if employee else False)

# Employee Management Routes
@bp.route('/employees')
@login_required
def employees_list():
    employees = (
        Employee.query
        .options(db.joinedload(Employee.pds), db.joinedload(Employee.department))
        .order_by(Employee.created_at.desc())
        .all()
    )
    departments = Department.query.all()
    employee = current_user.employee if current_user.employee else None
    return render_template('employees/list.html', employees=employees, departments=departments, employee=employee)


@bp.route('/about/users-manual')
@login_required
def users_manual():
    employee = current_user.employee if current_user.employee else None
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    role_label = (getattr(current_user, 'role', None) or 'User').strip().title()
    return render_template(
        'about/users_manual.html',
        employee=employee,
        viewer_role=role,
        viewer_role_label=role_label,
    )


@bp.route('/about/hrmdo-manual')
@login_required
def hrmdo_manual():
    employee = current_user.employee if current_user.employee else None
    return render_template('about/hrmdo_manual.html', employee=employee)


@bp.route('/about/platform')
@login_required
def about_platform():
    employee = current_user.employee if current_user.employee else None
    from app.platform_about import PLATFORM_ABOUT, PLATFORM_DEVELOPER
    return render_template(
        'about/platform.html',
        employee=employee,
        platform=PLATFORM_ABOUT,
        developer=PLATFORM_DEVELOPER,
    )


_DTR_PAYROLL_PROCESS_ROLES = frozenset({'admin', 'hr', 'payroll_maker'})
_DTR_PAYROLL_PROCESS_FILES = {
    'docx': (
        'DTR_to_Payroll_Process_Flow.docx',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'DTR_to_Payroll_Process_Flow.docx',
    ),
    'pdf': (
        'DTR_to_Payroll_Flowchart.pdf',
        'application/pdf',
        'DTR_to_Payroll_Flowchart.pdf',
    ),
}


def _require_dtr_payroll_process_viewer():
    """Admin, HR, or Payroll Maker may view the DTR→payroll process guide."""
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role in _DTR_PAYROLL_PROCESS_ROLES or _user_is_admin_or_hr():
        return None
    flash('Access denied. Admin, HR, or Payroll Maker role required.', 'error')
    return redirect(url_for('routes.dashboard'))


@bp.route('/about/dtr-payroll-process')
@login_required
def dtr_payroll_process():
    """Help: text process flow from DTR to payroll (Admin / HR / Payroll Maker)."""
    denied = _require_dtr_payroll_process_viewer()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    return render_template('about/dtr_payroll_process.html', employee=employee)


@bp.route('/about/dtr-payroll-process/download/<kind>')
@login_required
def dtr_payroll_process_download(kind: str):
    """Download Word process flow or PDF flowchart."""
    denied = _require_dtr_payroll_process_viewer()
    if denied:
        return denied
    meta = _DTR_PAYROLL_PROCESS_FILES.get((kind or '').strip().lower())
    if not meta:
        abort(404)
    filename, mimetype, download_name = meta
    docs_dir = os.path.join(os.path.dirname(__file__), 'static', 'docs')
    path = os.path.join(docs_dir, filename)
    if not os.path.isfile(path):
        flash('Process flow file is not available on the server.', 'error')
        return redirect(url_for('routes.dtr_payroll_process'))
    return send_file(
        path,
        mimetype=mimetype,
        as_attachment=True,
        download_name=download_name,
    )

@bp.route('/employees/add', methods=['GET', 'POST'])
@login_required
def employee_add():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    if request.method == 'POST':
        try:
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            middle_name = request.form.get('middle_name', '').strip()
            department_id = request.form.get('department_id')
            status_of_appointment = request.form.get('status_of_appointment', '').strip()
            position_raw = request.form.get('position', '').strip()
            jo_cos_raw = request.form.get('jo_cos_designation_id', '').strip()
            position, jo_cos_designation_id_val = _resolve_position_and_jo_cos_fk(
                status_of_appointment, position_raw, jo_cos_raw
            )
            nature_of_appointment = request.form.get('nature_of_appointment', '').strip()
            appointment_date = request.form.get('appointment_date')
            phone = request.form.get('phone', '').strip()
            mobile_no = request.form.get('mobile_no', '').strip()
            lgu_class_level = request.form.get('lgu_class_level', '').strip()
            salary_tranche = request.form.get('salary_tranche', '').strip()
            salary_grade_val = request.form.get('salary_grade', '').strip()
            salary_step_val = request.form.get('salary_step', '').strip()
            # Address fields
            residential_house_no = request.form.get('residential_house_no', '').strip()
            residential_street = request.form.get('residential_street', '').strip()
            residential_subdivision = request.form.get('residential_subdivision', '').strip()
            residential_barangay = request.form.get('residential_barangay', '').strip()
            residential_city = request.form.get('residential_city', '').strip()
            residential_province = request.form.get('residential_province', '').strip()
            residential_zip_code = request.form.get('residential_zip_code', '').strip()
            address = request.form.get('address', '').strip()  # Keep for backward compatibility
            status = (request.form.get('status') or 'active').strip().lower()
            if status not in EMPLOYEE_STATUSES:
                raise ValueError(f"Invalid status. Allowed: {', '.join(EMPLOYEE_STATUSES)}.")
            separation_date_raw = (request.form.get('separation_date') or '').strip()
            separation_type = _parse_employee_separation_type(request.form.get('separation_type'))

            separation_date = None
            if status == 'separated' and separation_date_raw:
                separation_date = datetime.strptime(separation_date_raw, '%Y-%m-%d').date()
            
            # Agency: National for MHO/RHU, else Local
            agency = None
            if department_id:
                dept = Department.query.get(int(department_id))
                agency = _agency_from_department(dept)

            # Prefer the official badge / employee ID from the form. Fall back to the
            # suggested 5-digit auto value only when the field is left blank.
            employee_id = (request.form.get('employee_id') or '').strip()
            if not employee_id:
                employee_id = _next_employee_id_5digit()
            employee_id = _validate_employee_badge_id(employee_id)
            if Employee.query.filter_by(employee_id=employee_id).first():
                raise ValueError(f'Employee ID "{employee_id}" is already in use.')

            new_employee = Employee(
                employee_id=employee_id,
                first_name=first_name,
                last_name=last_name,
                middle_name=middle_name if middle_name else None,
                position=position if position else None,
                jo_cos_designation_id=jo_cos_designation_id_val,
                department_id=int(department_id) if department_id else None,
                status_of_appointment=status_of_appointment if status_of_appointment else None,
                nature_of_appointment=nature_of_appointment if nature_of_appointment else None,
                appointment_date=datetime.strptime(appointment_date, '%Y-%m-%d').date() if appointment_date else None,
                phone=phone if phone else None,
                mobile_no=mobile_no if mobile_no else None,
                agency=agency,
                lgu_class_level=lgu_class_level if lgu_class_level else None,
                salary_tranche=salary_tranche if salary_tranche else None,
                salary_grade=int(salary_grade_val) if salary_grade_val else None,
                salary_step=int(salary_step_val) if salary_step_val else None,
                flexible_worktime=False,
                flexible_start_time=None,
                flexible_end_time=None,
                residential_house_no=residential_house_no if residential_house_no else None,
                residential_street=residential_street if residential_street else None,
                residential_subdivision=residential_subdivision if residential_subdivision else None,
                residential_barangay=residential_barangay if residential_barangay else None,
                residential_city=residential_city if residential_city else None,
                residential_province=residential_province if residential_province else None,
                residential_zip_code=residential_zip_code if residential_zip_code else None,
                address=address if address else None,  # Keep for backward compatibility
                status=status,
                separation_date=separation_date if status == 'separated' else None,
                separation_type=separation_type if status == 'separated' else None,
            )

            try:
                db.session.add(new_employee)
                db.session.flush()  # Assign new_employee.id and verify unique constraints
            except IntegrityError:
                db.session.rollback()
                raise ValueError(f'Employee ID "{employee_id}" is already in use.')
            # Get user email if employee has a user account
            user_email = None
            if new_employee.user_id:
                user = User.query.get(new_employee.user_id)
                if user:
                    user_email = user.email
            
            # Create corresponding PDS record with address, mobile, phone, and email
            pds = EmployeePDS(
                employee_id=new_employee.id,
                surname=new_employee.last_name,
                first_name=new_employee.first_name,
                middle_name=new_employee.middle_name,
                telephone_no=phone if phone else None,
                mobile_no=mobile_no if mobile_no else None,
                email_address=user_email,
                residential_house_no=residential_house_no if residential_house_no else None,
                residential_street=residential_street if residential_street else None,
                residential_subdivision=residential_subdivision if residential_subdivision else None,
                residential_barangay=residential_barangay if residential_barangay else None,
                residential_city=residential_city if residential_city else None,
                residential_province=residential_province if residential_province else None,
                residential_zip_code=residential_zip_code if residential_zip_code else None
            )
            db.session.add(pds)
            ensure_appointment_spl_wl_grant(
                new_employee.id,
                created_by_user_id=getattr(current_user, 'id', None),
            )
            _sync_employee_flexi_days(new_employee.id, [])
            _seed_opening_appointment_history(new_employee)
            db.session.commit()
            flash('Employee added successfully.', 'success')
            return redirect(url_for('routes.employees_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding employee: {str(e)}', 'error')
            departments = Department.query.all()
            positions = Position.query.order_by(Position.title).all()
            salary_grades = SalaryGrade.query.all()
            employee = current_user.employee if current_user.employee else None
            salary_grades_json = _serialize_salary_grades(salary_grades)
            return render_template(
                'employees/form.html',
                departments=departments,
                positions=positions,
                salary_grades=salary_grades,
                salary_grades_json=salary_grades_json,
                jo_cos_designations=_jo_cos_designations_ordered(),
                next_employee_id=_next_employee_id_5digit(),
                employee=employee,
                **_employee_form_flexi_kwargs(),
            )
    
    departments = Department.query.all()
    positions = Position.query.order_by(Position.title).all()
    salary_grades = SalaryGrade.query.all()
    employee = current_user.employee if current_user.employee else None
    salary_grades_json = _serialize_salary_grades(salary_grades)
    return render_template(
        'employees/form.html',
        departments=departments,
        positions=positions,
        salary_grades=salary_grades,
        salary_grades_json=salary_grades_json,
        jo_cos_designations=_jo_cos_designations_ordered(),
        next_employee_id=_next_employee_id_5digit(),
        employee=employee,
        **_employee_form_flexi_kwargs(),
    )

@bp.route('/employees/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def employee_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp = Employee.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            employee_id = request.form.get('employee_id', '').strip()
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            middle_name = request.form.get('middle_name', '').strip()
            department_id = request.form.get('department_id')
            status_of_appointment = request.form.get('status_of_appointment', '').strip()
            position_raw = request.form.get('position', '').strip()
            jo_cos_raw = request.form.get('jo_cos_designation_id', '').strip()
            position, jo_cos_designation_id_val = _resolve_position_and_jo_cos_fk(
                status_of_appointment, position_raw, jo_cos_raw
            )
            nature_of_appointment = request.form.get('nature_of_appointment', '').strip()
            appointment_date = request.form.get('appointment_date')
            phone = request.form.get('phone', '').strip()
            mobile_no = request.form.get('mobile_no', '').strip()
            lgu_class_level = request.form.get('lgu_class_level', '').strip()
            salary_tranche = request.form.get('salary_tranche', '').strip()
            salary_grade_val = request.form.get('salary_grade', '').strip()
            salary_step_val = request.form.get('salary_step', '').strip()
            # Address fields
            residential_house_no = request.form.get('residential_house_no', '').strip()
            residential_street = request.form.get('residential_street', '').strip()
            residential_subdivision = request.form.get('residential_subdivision', '').strip()
            residential_barangay = request.form.get('residential_barangay', '').strip()
            residential_city = request.form.get('residential_city', '').strip()
            residential_province = request.form.get('residential_province', '').strip()
            residential_zip_code = request.form.get('residential_zip_code', '').strip()
            address = request.form.get('address', '').strip()  # Keep for backward compatibility
            status = (request.form.get('status') or 'active').strip().lower()
            if status not in EMPLOYEE_STATUSES:
                raise ValueError(f"Invalid status. Allowed: {', '.join(EMPLOYEE_STATUSES)}.")
            separation_date_raw = (request.form.get('separation_date') or '').strip()
            separation_type = _parse_employee_separation_type(request.form.get('separation_type'))

            separation_date = None
            if status == 'separated' and separation_date_raw:
                separation_date = datetime.strptime(separation_date_raw, '%Y-%m-%d').date()

            # Employee ID: only Admin may change an existing badge number
            old_employee_id = (emp.employee_id or '').strip()
            if not _can_edit_employee_id():
                employee_id = old_employee_id
            if not employee_id:
                raise ValueError('Employee ID is required.')
            if employee_id != old_employee_id:
                employee_id = _validate_employee_badge_id(employee_id)
            
            # Check if employee_id already exists (excluding current employee)
            existing = Employee.query.filter_by(employee_id=employee_id).first()
            if existing and existing.id != id:
                flash('Employee ID already exists.', 'error')
                departments = Department.query.all()
                positions = Position.query.order_by(Position.title).all()
                salary_grades = SalaryGrade.query.all()
                employee = current_user.employee if current_user.employee else None
                salary_grades_json = _serialize_salary_grades(salary_grades)
                return render_template(
                    'employees/form.html',
                    emp=emp,
                    departments=departments,
                    positions=positions,
                    salary_grades=salary_grades,
                    salary_grades_json=salary_grades_json,
                    jo_cos_designations=_jo_cos_designations_ordered(),
                    employee=employee,
                    can_edit_employee_id=_can_edit_employee_id(),
                    **_employee_form_flexi_kwargs(emp),
                )

            if employee_id != old_employee_id:
                # Keep login badge in sync when the employee has a user account
                linked_user = None
                if emp.user_id:
                    linked_user = User.query.get(emp.user_id)
                if linked_user is None:
                    linked_user = User.query.filter_by(employee_id=old_employee_id).first()
                if linked_user:
                    clash = User.query.filter(
                        User.employee_id == employee_id,
                        User.id != linked_user.id,
                    ).first()
                    if clash:
                        raise ValueError(
                            f'Cannot change Employee ID to "{employee_id}": '
                            'another user account already uses that ID.'
                        )
                    linked_user.employee_id = employee_id

            # Capture old appointment-related values for history (before update)
            old_appointment_date = emp.appointment_date
            old_status_of_appointment = emp.status_of_appointment
            old_nature_of_appointment = emp.nature_of_appointment
            old_agency = emp.agency
            old_lgu_class_level = emp.lgu_class_level
            old_salary_tranche = emp.salary_tranche
            old_salary_grade = emp.salary_grade
            old_salary_step = emp.salary_step

            # Agency follows salary schedule: National for MHO/RHU, else Local
            agency = None
            dept = None
            if department_id:
                dept = Department.query.get(int(department_id))
                agency = _agency_from_department(dept)

            # New appointment/salary values (for change detection)
            new_appointment_date = datetime.strptime(appointment_date, '%Y-%m-%d').date() if appointment_date else None
            new_status = status_of_appointment if status_of_appointment else None
            new_nature = nature_of_appointment if nature_of_appointment else None
            new_lgu = lgu_class_level if lgu_class_level else None
            new_tranche = salary_tranche if salary_tranche else None
            new_sal_grade = int(salary_grade_val) if salary_grade_val else None
            new_sal_step = int(salary_step_val) if salary_step_val else None

            # Update employee
            emp.employee_id = employee_id
            emp.first_name = first_name
            emp.last_name = last_name
            emp.middle_name = middle_name if middle_name else None
            emp.position = position if position else None
            emp.jo_cos_designation_id = jo_cos_designation_id_val
            emp.department_id = int(department_id) if department_id else None
            emp.status_of_appointment = status_of_appointment if status_of_appointment else None
            emp.nature_of_appointment = nature_of_appointment if nature_of_appointment else None
            # Original appointment date stays fixed unless Admin corrects it.
            if _can_edit_employee_id():
                emp.appointment_date = new_appointment_date
            emp.phone = phone if phone else None
            emp.mobile_no = mobile_no if mobile_no else None
            emp.agency = agency
            emp.lgu_class_level = lgu_class_level if lgu_class_level else None
            emp.salary_tranche = salary_tranche if salary_tranche else None
            emp.salary_grade = int(salary_grade_val) if salary_grade_val else None
            emp.salary_step = int(salary_step_val) if salary_step_val else None
            emp.flexible_worktime = False
            emp.flexible_start_time = None
            emp.flexible_end_time = None
            emp.residential_house_no = residential_house_no if residential_house_no else None
            emp.residential_street = residential_street if residential_street else None
            emp.residential_subdivision = residential_subdivision if residential_subdivision else None
            emp.residential_barangay = residential_barangay if residential_barangay else None
            emp.residential_city = residential_city if residential_city else None
            emp.residential_province = residential_province if residential_province else None
            emp.residential_zip_code = residential_zip_code if residential_zip_code else None
            emp.address = address if address else None  # Keep for backward compatibility
            emp.status = status
            if status == 'separated':
                emp.separation_date = separation_date
                emp.separation_type = separation_type
            else:
                emp.separation_date = None
                emp.separation_type = None
            emp.updated_at = datetime.utcnow()
            
            # Get user email if employee has a user account
            user_email = None
            if emp.user_id:
                user = User.query.get(emp.user_id)
                if user:
                    user_email = user.email
            
            # Sync name + address, mobile, phone, and email to employee_pds table
            pds = EmployeePDS.query.filter_by(employee_id=id).first()
            if pds:
                pds.surname = emp.last_name
                pds.first_name = emp.first_name
                pds.middle_name = emp.middle_name
                pds.telephone_no = phone if phone else None
                pds.mobile_no = mobile_no if mobile_no else None
                pds.email_address = user_email
                pds.residential_house_no = residential_house_no if residential_house_no else None
                pds.residential_street = residential_street if residential_street else None
                pds.residential_subdivision = residential_subdivision if residential_subdivision else None
                pds.residential_barangay = residential_barangay if residential_barangay else None
                pds.residential_city = residential_city if residential_city else None
                pds.residential_province = residential_province if residential_province else None
                pds.residential_zip_code = residential_zip_code if residential_zip_code else None
            else:
                # Create PDS record if it doesn't exist
                pds = EmployeePDS(
                    employee_id=id,
                    surname=emp.last_name,
                    first_name=emp.first_name,
                    middle_name=emp.middle_name,
                    telephone_no=phone if phone else None,
                    mobile_no=mobile_no if mobile_no else None,
                    email_address=user_email,
                    residential_house_no=residential_house_no if residential_house_no else None,
                    residential_street=residential_street if residential_street else None,
                    residential_subdivision=residential_subdivision if residential_subdivision else None,
                    residential_barangay=residential_barangay if residential_barangay else None,
                    residential_city=residential_city if residential_city else None,
                    residential_province=residential_province if residential_province else None,
                    residential_zip_code=residential_zip_code if residential_zip_code else None
                )
                db.session.add(pds)

            # If appointment/salary-related fields changed, append to history.
            # Original appointment_date is not used as effectivity for later changes.
            appointment_changed = (
                old_status_of_appointment != new_status
                or old_nature_of_appointment != new_nature
                or old_agency != agency
                or old_lgu_class_level != new_lgu
                or old_salary_tranche != new_tranche
                or old_salary_grade != new_sal_grade
                or old_salary_step != new_sal_step
            )
            if _can_edit_employee_id() and old_appointment_date != emp.appointment_date:
                appointment_changed = True
            if appointment_changed:
                effectivity = date.today()
                if _can_edit_employee_id() and new_appointment_date and new_appointment_date != old_appointment_date:
                    # Admin corrected original date — keep effectivity as today for the update row
                    effectivity = date.today()
                db.session.add(_build_appointment_history_entry(
                    emp,
                    change_type=APPOINTMENT_CHANGE_UPDATE,
                    effectivity_date=effectivity,
                    from_sal_step=old_salary_step,
                    to_sal_step=emp.salary_step,
                    remarks='Updated via Employees edit',
                ))

            ensure_appointment_spl_wl_grant(
                emp.id,
                created_by_user_id=getattr(current_user, 'id', None),
            )
            _sync_employee_flexi_days(emp.id, [])

            from app.employee_id_photo_service import link_employee_id_photo_by_ermt_number
            photo_number = (request.form.get('id_photo_ermt_number') or '').strip()
            if photo_number:
                ok, photo_msg = link_employee_id_photo_by_ermt_number(emp, photo_number)
                if not ok:
                    raise ValueError(photo_msg)
            else:
                emp.id_photo_ermt_record_id = None

            db.session.commit()
            flash('Employee updated successfully.', 'success')
            return redirect(url_for('routes.employees_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating employee: {str(e)}', 'error')
            departments = Department.query.all()
            positions = Position.query.order_by(Position.title).all()
            salary_grades = SalaryGrade.query.all()
            employee = current_user.employee if current_user.employee else None
            salary_grades_json = _serialize_salary_grades(salary_grades)
            return render_template(
                'employees/form.html',
                emp=emp,
                departments=departments,
                positions=positions,
                salary_grades=salary_grades,
                salary_grades_json=salary_grades_json,
                jo_cos_designations=_jo_cos_designations_ordered(),
                employee=employee,
                can_edit_employee_id=_can_edit_employee_id(),
                **_employee_form_flexi_kwargs(emp),
            )
    
    departments = Department.query.all()
    positions = Position.query.order_by(Position.title).all()
    salary_grades = SalaryGrade.query.all()
    employee = current_user.employee if current_user.employee else None
    salary_grades_json = _serialize_salary_grades(salary_grades)
    return render_template(
        'employees/form.html',
        emp=emp,
        departments=departments,
        positions=positions,
        salary_grades=salary_grades,
        salary_grades_json=salary_grades_json,
        jo_cos_designations=_jo_cos_designations_ordered(),
        employee=employee,
        can_edit_employee_id=_can_edit_employee_id(),
        **_employee_form_flexi_kwargs(emp),
    )

def _purge_employee_dependent_rows(
    employee_pk: int,
    *,
    deleted_by_user_id=None,
    deleted_by_username=None,
):
    """
    Remove / unlink all NOT-NULL FK rows that block employee delete.

    SQLAlchemy otherwise SET NULLs relationships like dtr_upload_batch_items.employee_id,
    which violates NOT NULL constraints.
    """
    eid = employee_pk

    # Soft-nulls (nullable FKs)
    Department.query.filter_by(manager_id=eid).update(
        {Department.manager_id: None},
        synchronize_session=False,
    )

    # Leave ledger: keep deletion audit trail, then remove rows
    ledger_rows = LeaveLedger.query.filter_by(employee_id=eid).all()
    for ledger_entry in ledger_rows:
        record_leave_ledger_deletion(
            ledger_entry,
            deleted_by_user_id=deleted_by_user_id,
            deleted_by_username=deleted_by_username,
            source='employee_delete',
        )

    # Delete in dependency-friendly order. Many of these have NOT NULL employee_id
    # and ORM backrefs that would otherwise try to null the FK on parent delete.
    delete_specs = (
        (DtrUploadPunchFlag, 'employee_id'),
        (DtrUploadPunch, 'employee_id'),
        (DtrUploadBatchItem, 'employee_id'),
        (DtrJustification, 'employee_id'),
        (DtrAccomplishmentReport, 'employee_id'),
        (DtrMonthlySubmission, 'employee_id'),
        (DtrQuincenaWorktimeSummary, 'employee_id'),
        (WorkHours, 'employee_id'),
        (FlexibleWorktime, 'employee_id'),
        (OvertimeAuthorization, 'employee_id'),
        (JoCosExtendService, 'employee_id'),
        (JoCosOvertime, 'employee_id'),
        (JoCosOvertimeLedger, 'employee_id'),
        (JoCosOvertimeOffsetRequest, 'employee_id'),
        (JoCosPayslip, 'employee_id'),
        (PlantillaPayslip, 'employee_id'),
        (GsisLoanDeduction, 'employee_id'),
        (GsisLoanRecord, 'employee_id'),
        (GsisContribution, 'employee_id'),
        (PhicContribution, 'employee_id'),
        (LbpBillingDeduction, 'employee_id'),
        (LbpBillingRecord, 'employee_id'),
        (JofemcoBillingDeduction, 'employee_id'),
        (JofemcoBillingRecord, 'employee_id'),
        (WithholdingTaxDeduction, 'employee_id'),
        (WithholdingTaxRecord, 'employee_id'),
        (HdmfContributionDeduction, 'employee_id'),
        (HdmfContributionRecord, 'employee_id'),
        (HdmfLoanDeduction, 'employee_id'),
        (HdmfLoanRecord, 'employee_id'),
        (LeaveBalance, 'employee_id'),
        (LeaveLedger, 'employee_id'),
        (LeaveRequest, 'employee_id'),
        (DailyTimeRecord, 'employee_id'),
        (Attendance, 'employee_id'),
        (EmployeeAppointmentHistory, 'emp_id'),
        (EmployeeFlexiDay, 'employee_id'),
        (EmployeePDS, 'employee_id'),
    )
    for model, col in delete_specs:
        model.query.filter_by(**{col: eid}).delete(synchronize_session=False)


@bp.route('/employees/delete/<int:id>', methods=['POST'])
@login_required
def employee_delete(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp = Employee.query.get_or_404(id)
    
    try:
        # Check if employee has associated user
        if emp.user:
            flash('Cannot delete employee with associated user account. Delete the user account first.', 'error')
            return redirect(url_for('routes.employees_list'))

        _purge_employee_dependent_rows(
            emp.id,
            deleted_by_user_id=current_user.id,
            deleted_by_username=getattr(current_user, 'username', None),
        )
        db.session.delete(emp)
        db.session.commit()
        flash('Employee deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting employee: {str(e)}', 'error')
    
    return redirect(url_for('routes.employees_list'))

@bp.route('/employees/<int:id>/pds', methods=['GET', 'POST'])
@login_required
def employee_pds(id):
    emp = Employee.query.get_or_404(id)
    employee = current_user.employee if current_user.employee else None
    can_edit_pds = _can_edit_employee_pds(emp)

    # Get or create PDS record
    pds = EmployeePDS.query.filter_by(employee_id=id).first()

    if request.method == 'POST':
        if not can_edit_pds:
            flash('You do not have permission to update this PDS.', 'error')
            return redirect(url_for('routes.employee_pds', id=id))
        try:
            if not pds:
                pds = EmployeePDS(employee_id=id)
                db.session.add(pds)
            
            # Always sync name fields from Employee table (these should always be synced)
            pds.surname = emp.last_name
            pds.first_name = emp.first_name
            pds.middle_name = emp.middle_name
            
            # Check which section is being saved FIRST
            section = request.form.get('section', 'personal_information')
            
            # Only process Personal Information if this is the personal_information section
            if section == 'personal_information':
                # Personal Information
                pds.name_extension = Employee.normalize_name_extension(
                    request.form.get('name_extension', '')
                ) or None
                dob = request.form.get('date_of_birth', '').strip()
                pds.date_of_birth = datetime.strptime(dob, '%Y-%m-%d').date() if dob else None
                pds.place_of_birth = request.form.get('place_of_birth', '').strip() or None
                pds.sex_at_birth = request.form.get('sex_at_birth', '').strip() or None
                pds.civil_status = request.form.get('civil_status', '').strip() or None
                pds.civil_status_other = request.form.get('civil_status_other', '').strip() or None
                pds.height = request.form.get('height', '').strip() or None
                pds.weight = request.form.get('weight', '').strip() or None
                pds.blood_type = request.form.get('blood_type', '').strip() or None
                pds.umid_id = request.form.get('umid_id', '').strip() or None
                pds.pagibig_id = request.form.get('pagibig_id', '').strip() or None
                pds.philhealth_no = request.form.get('philhealth_no', '').strip() or None
                pds.philsys_number = request.form.get('philsys_number', '').strip() or None
                pds.tin_no = request.form.get('tin_no', '').strip() or None
                # Item 15: Agency Employee No. is read-only and comes from employees.employee_id
                # It should not be saved from the form
                
                # Citizenship
                pds.citizenship = request.form.get('citizenship', '').strip() or None
                pds.dual_citizenship_type = request.form.get('dual_citizenship_type', '').strip() or None
                pds.dual_citizenship_country = request.form.get('dual_citizenship_country', '').strip() or None
                
                # Residential Address
                pds.residential_house_no = request.form.get('residential_house_no', '').strip() or None
                pds.residential_street = request.form.get('residential_street', '').strip() or None
                pds.residential_subdivision = request.form.get('residential_subdivision', '').strip() or None
                pds.residential_barangay = request.form.get('residential_barangay', '').strip() or None
                pds.residential_city = request.form.get('residential_city', '').strip() or None
                pds.residential_province = request.form.get('residential_province', '').strip() or None
                pds.residential_zip_code = request.form.get('residential_zip_code', '').strip() or None
                
                # Permanent Address
                same_as_residential = request.form.get('same_as_residential') == 'on'
                if same_as_residential:
                    # Copy residential address to permanent address
                    pds.permanent_house_no = emp.residential_house_no if emp.residential_house_no else pds.residential_house_no
                    pds.permanent_street = emp.residential_street if emp.residential_street else pds.residential_street
                    pds.permanent_subdivision = emp.residential_subdivision if emp.residential_subdivision else pds.residential_subdivision
                    pds.permanent_barangay = emp.residential_barangay if emp.residential_barangay else pds.residential_barangay
                    pds.permanent_city = emp.residential_city if emp.residential_city else pds.residential_city
                    pds.permanent_province = emp.residential_province if emp.residential_province else pds.residential_province
                    pds.permanent_zip_code = emp.residential_zip_code if emp.residential_zip_code else pds.residential_zip_code
                else:
                    # Use permanent address from form
                    pds.permanent_house_no = request.form.get('permanent_house_no', '').strip() or None
                    pds.permanent_street = request.form.get('permanent_street', '').strip() or None
                    pds.permanent_subdivision = request.form.get('permanent_subdivision', '').strip() or None
                    pds.permanent_barangay = request.form.get('permanent_barangay', '').strip() or None
                    pds.permanent_city = request.form.get('permanent_city', '').strip() or None
                    pds.permanent_province = request.form.get('permanent_province', '').strip() or None
                    pds.permanent_zip_code = request.form.get('permanent_zip_code', '').strip() or None
                
                # Contact Information
                # Telephone, mobile, email, and address are synced from Employee/User table, not from form
                pds.telephone_no = emp.phone if emp.phone else pds.telephone_no
                pds.mobile_no = emp.mobile_no if emp.mobile_no else pds.mobile_no
                # Get email from User table if employee has a user account
                if emp.user_id:
                    user = User.query.get(emp.user_id)
                    if user:
                        pds.email_address = user.email
                pds.residential_house_no = emp.residential_house_no if emp.residential_house_no else pds.residential_house_no
                pds.residential_street = emp.residential_street if emp.residential_street else pds.residential_street
                pds.residential_subdivision = emp.residential_subdivision if emp.residential_subdivision else pds.residential_subdivision
                pds.residential_barangay = emp.residential_barangay if emp.residential_barangay else pds.residential_barangay
                pds.residential_city = emp.residential_city if emp.residential_city else pds.residential_city
                pds.residential_province = emp.residential_province if emp.residential_province else pds.residential_province
                pds.residential_zip_code = emp.residential_zip_code if emp.residential_zip_code else pds.residential_zip_code
                
                db.session.commit()
                flash('Personal information saved successfully.', 'success')
            elif section == 'family_background':
                # Section II: Family Background
                # Section 22: Spouse's Information
                pds.spouse_surname = request.form.get('spouse_surname', '').strip() or None
                pds.spouse_first_name = request.form.get('spouse_first_name', '').strip() or None
                pds.spouse_middle_name = request.form.get('spouse_middle_name', '').strip() or None
                pds.spouse_name_extension = request.form.get('spouse_name_extension', '').strip() or None
                pds.spouse_occupation = request.form.get('spouse_occupation', '').strip() or None
                pds.spouse_employer = request.form.get('spouse_employer', '').strip() or None
                pds.spouse_business_address = request.form.get('spouse_business_address', '').strip() or None
                pds.spouse_telephone_no = request.form.get('spouse_telephone_no', '').strip() or None
                
                # Section 23: Children's Information (up to 20 children)
                for i in range(1, 21):
                    child_name = request.form.get(f'child{i}_name', '').strip() or None
                    child_dob = request.form.get(f'child{i}_date_of_birth', '').strip()
                    child_dob_date = datetime.strptime(child_dob, '%Y-%m-%d').date() if child_dob else None
                    setattr(pds, f'child{i}_name', child_name)
                    setattr(pds, f'child{i}_date_of_birth', child_dob_date)
                
                # Section 24: Father's Information
                pds.father_surname = request.form.get('father_surname', '').strip() or None
                pds.father_first_name = request.form.get('father_first_name', '').strip() or None
                pds.father_middle_name = request.form.get('father_middle_name', '').strip() or None
                pds.father_name_extension = request.form.get('father_name_extension', '').strip() or None
                
                # Section 25: Mother's Maiden Name
                pds.mother_surname = request.form.get('mother_surname', '').strip() or None
                pds.mother_first_name = request.form.get('mother_first_name', '').strip() or None
                pds.mother_middle_name = request.form.get('mother_middle_name', '').strip() or None
                
                db.session.commit()
                flash('Family background saved successfully.', 'success')
            elif section == 'educational_background':
                # Section III: Educational Background
                # Save all educational background entries (5 entries per level × 5 levels × 7 fields)
                levels = ['elementary', 'secondary', 'vocational', 'college', 'graduate']
                for level in levels:
                    for entry_num in range(1, 6):
                        school = request.form.get(f'{level}{entry_num}_school', '').strip() or None
                        degree = request.form.get(f'{level}{entry_num}_degree', '').strip() or None
                        from_date = request.form.get(f'{level}{entry_num}_from', '').strip() or None
                        to_date = request.form.get(f'{level}{entry_num}_to', '').strip() or None
                        highest_level = request.form.get(f'{level}{entry_num}_highest_level', '').strip() or None
                        year_graduated = request.form.get(f'{level}{entry_num}_year_graduated', '').strip() or None
                        honors = request.form.get(f'{level}{entry_num}_honors', '').strip() or None
                        
                        setattr(pds, f'{level}{entry_num}_school', school)
                        setattr(pds, f'{level}{entry_num}_degree', degree)
                        setattr(pds, f'{level}{entry_num}_from', from_date)
                        setattr(pds, f'{level}{entry_num}_to', to_date)
                        setattr(pds, f'{level}{entry_num}_highest_level', highest_level)
                        setattr(pds, f'{level}{entry_num}_year_graduated', year_graduated)
                        setattr(pds, f'{level}{entry_num}_honors', honors)
                
                db.session.commit()
                flash('Educational background saved successfully.', 'success')
            elif section == 'civil_service_eligibility':
                # Section IV: Civil Service Eligibility
                # Save all civil service eligibility entries (up to 10 entries)
                for entry_num in range(1, 11):
                    eligibility = request.form.get(f'civil_service{entry_num}_eligibility', '').strip() or None
                    rating = request.form.get(f'civil_service{entry_num}_rating', '').strip() or None
                    date = request.form.get(f'civil_service{entry_num}_date', '').strip() or None
                    place = request.form.get(f'civil_service{entry_num}_place', '').strip() or None
                    license_number = request.form.get(f'civil_service{entry_num}_license_number', '').strip() or None
                    license_valid_until = request.form.get(f'civil_service{entry_num}_license_valid_until', '').strip() or None
                    
                    setattr(pds, f'civil_service{entry_num}_eligibility', eligibility)
                    setattr(pds, f'civil_service{entry_num}_rating', rating)
                    setattr(pds, f'civil_service{entry_num}_date', date)
                    setattr(pds, f'civil_service{entry_num}_place', place)
                    setattr(pds, f'civil_service{entry_num}_license_number', license_number)
                    setattr(pds, f'civil_service{entry_num}_license_valid_until', license_valid_until)
                
                db.session.commit()
                flash('Civil service eligibility saved successfully.', 'success')
            elif section == 'work_experience':
                # Section V: Work Experience
                # Save all work experience entries (up to 20 entries)
                for entry_num in range(1, 21):
                    from_date = request.form.get(f'work_exp{entry_num}_from_date', '').strip() or None
                    to_date = request.form.get(f'work_exp{entry_num}_to_date', '').strip() or None
                    position_title = request.form.get(f'work_exp{entry_num}_position_title', '').strip() or None
                    department = request.form.get(f'work_exp{entry_num}_department', '').strip() or None
                    status_of_appointment = request.form.get(f'work_exp{entry_num}_status_of_appointment', '').strip() or None
                    govt_service = request.form.get(f'work_exp{entry_num}_govt_service', '').strip() or None
                    description_of_duties = request.form.get(f'work_exp{entry_num}_description_of_duties', '').strip() or None
                    
                    setattr(pds, f'work_exp{entry_num}_from_date', from_date)
                    setattr(pds, f'work_exp{entry_num}_to_date', to_date)
                    setattr(pds, f'work_exp{entry_num}_position_title', position_title)
                    setattr(pds, f'work_exp{entry_num}_department', department)
                    setattr(pds, f'work_exp{entry_num}_status_of_appointment', status_of_appointment)
                    setattr(pds, f'work_exp{entry_num}_govt_service', govt_service)
                    setattr(pds, f'work_exp{entry_num}_description_of_duties', description_of_duties)
                
                db.session.commit()
                flash('Work experience saved successfully.', 'success')
            elif section == 'voluntary_work':
                # Section VI: Voluntary Work
                # Save all voluntary work entries (up to 10 entries)
                for entry_num in range(1, 11):
                    organization = request.form.get(f'voluntary{entry_num}_organization', '').strip() or None
                    from_date = request.form.get(f'voluntary{entry_num}_from_date', '').strip() or None
                    to_date = request.form.get(f'voluntary{entry_num}_to_date', '').strip() or None
                    number_of_hours = request.form.get(f'voluntary{entry_num}_number_of_hours', '').strip() or None
                    position = request.form.get(f'voluntary{entry_num}_position', '').strip() or None
                    
                    setattr(pds, f'voluntary{entry_num}_organization', organization)
                    setattr(pds, f'voluntary{entry_num}_from_date', from_date)
                    setattr(pds, f'voluntary{entry_num}_to_date', to_date)
                    setattr(pds, f'voluntary{entry_num}_number_of_hours', number_of_hours)
                    setattr(pds, f'voluntary{entry_num}_position', position)
                
                db.session.commit()
                flash('Voluntary work saved successfully.', 'success')
            elif section == 'learning_development':
                # Section VII: Learning and Development (L&D) Interventions/Training Programs
                # Save all L&D entries (up to 50 entries)
                for entry_num in range(1, 51):
                    title = request.form.get(f'ld{entry_num}_title', '').strip() or None
                    from_date = request.form.get(f'ld{entry_num}_from_date', '').strip() or None
                    to_date = request.form.get(f'ld{entry_num}_to_date', '').strip() or None
                    number_of_hours = request.form.get(f'ld{entry_num}_number_of_hours', '').strip() or None
                    type_of_ld = request.form.get(f'ld{entry_num}_type_of_ld', '').strip() or None
                    conducted_by = request.form.get(f'ld{entry_num}_conducted_by', '').strip() or None
                    
                    setattr(pds, f'ld{entry_num}_title', title)
                    setattr(pds, f'ld{entry_num}_from_date', from_date)
                    setattr(pds, f'ld{entry_num}_to_date', to_date)
                    setattr(pds, f'ld{entry_num}_number_of_hours', number_of_hours)
                    setattr(pds, f'ld{entry_num}_type_of_ld', type_of_ld)
                    setattr(pds, f'ld{entry_num}_conducted_by', conducted_by)
                
                db.session.commit()
                flash('Learning and Development saved successfully.', 'success')
            elif section == 'other_information':
                # Section VIII: Other Information
                # Save Special Skills and Hobbies (Item 31) - up to 15 entries
                for entry_num in range(1, 16):
                    special_skill = request.form.get(f'special_skill{entry_num}', '').strip() or None
                    setattr(pds, f'special_skill{entry_num}', special_skill)
                
                # Save Non-Academic Distinctions/Recognition (Item 32) - up to 15 entries
                for entry_num in range(1, 16):
                    non_academic = request.form.get(f'non_academic{entry_num}', '').strip() or None
                    setattr(pds, f'non_academic{entry_num}', non_academic)
                
                # Save Membership in Association/Organization (Item 33) - up to 15 entries
                for entry_num in range(1, 16):
                    membership = request.form.get(f'membership{entry_num}', '').strip() or None
                    setattr(pds, f'membership{entry_num}', membership)
                
                # Save Item 34: Related by consanguinity or affinity
                related_third_degree = request.form.get('related_third_degree_yesno') == 'YES'
                related_fourth_degree = request.form.get('related_fourth_degree_yesno') == 'YES'
                related_details = request.form.get('related_details', '').strip() or None
                pds.related_third_degree = related_third_degree
                pds.related_fourth_degree = related_fourth_degree
                pds.related_details = related_details
                
                # Save Item 35: Administrative offense and criminal charges
                admin_offense_guilty = request.form.get('admin_offense_yesno') == 'YES'
                admin_offense_details = request.form.get('admin_offense_details', '').strip() or None
                criminally_charged = request.form.get('criminally_charged_yesno') == 'YES'
                criminal_charge_details = request.form.get('criminal_charge_details', '').strip() or None
                criminal_charge_date_filed = request.form.get('criminal_charge_date_filed', '').strip() or None
                criminal_charge_status = request.form.get('criminal_charge_status', '').strip() or None
                pds.admin_offense_guilty = admin_offense_guilty
                pds.admin_offense_details = admin_offense_details
                pds.criminally_charged = criminally_charged
                pds.criminal_charge_details = criminal_charge_details
                pds.criminal_charge_date_filed = criminal_charge_date_filed
                pds.criminal_charge_status = criminal_charge_status
                
                # Save Item 36: Convicted of crime
                convicted_crime = request.form.get('convicted_crime_yesno') == 'YES'
                convicted_crime_details = request.form.get('convicted_crime_details', '').strip() or None
                pds.convicted_crime = convicted_crime
                pds.convicted_crime_details = convicted_crime_details
                
                # Save Item 37: Separated from service
                separated_from_service = request.form.get('separated_from_service_yesno') == 'YES'
                separated_from_service_details = request.form.get('separated_from_service_details', '').strip() or None
                pds.separated_from_service = separated_from_service
                pds.separated_from_service_details = separated_from_service_details
                
                # Save Item 38: Political involvement
                candidate_in_election = request.form.get('candidate_in_election_yesno') == 'YES'
                candidate_in_election_details = request.form.get('candidate_in_election_details', '').strip() or None
                resigned_for_election = request.form.get('resigned_for_election_yesno') == 'YES'
                resigned_for_election_details = request.form.get('resigned_for_election_details', '').strip() or None
                pds.candidate_in_election = candidate_in_election
                pds.candidate_in_election_details = candidate_in_election_details
                pds.resigned_for_election = resigned_for_election
                pds.resigned_for_election_details = resigned_for_election_details
                
                # Save Item 39: Immigration status
                immigrant_permanent_resident = request.form.get('immigrant_permanent_resident_yesno') == 'YES'
                immigrant_country = request.form.get('immigrant_country', '').strip() or None
                pds.immigrant_permanent_resident = immigrant_permanent_resident
                pds.immigrant_country = immigrant_country
                
                # Save Item 40: Special groups
                indigenous_group_member = request.form.get('indigenous_group_member_yesno') == 'YES'
                indigenous_group_specify = request.form.get('indigenous_group_specify', '').strip() or None
                person_with_disability = request.form.get('person_with_disability_yesno') == 'YES'
                disability_id_no = request.form.get('disability_id_no', '').strip() or None
                solo_parent = request.form.get('solo_parent_yesno') == 'YES'
                solo_parent_id_no = request.form.get('solo_parent_id_no', '').strip() or None
                pds.indigenous_group_member = indigenous_group_member
                pds.indigenous_group_specify = indigenous_group_specify
                pds.person_with_disability = person_with_disability
                pds.disability_id_no = disability_id_no
                pds.solo_parent = solo_parent
                pds.solo_parent_id_no = solo_parent_id_no
                
                # Save Item 41: References (3 references)
                for ref_num in range(1, 4):
                    reference_name = request.form.get(f'reference{ref_num}_name', '').strip() or None
                    reference_address = request.form.get(f'reference{ref_num}_address', '').strip() or None
                    reference_contact = request.form.get(f'reference{ref_num}_contact', '').strip() or None
                    setattr(pds, f'reference{ref_num}_name', reference_name)
                    setattr(pds, f'reference{ref_num}_address', reference_address)
                    setattr(pds, f'reference{ref_num}_contact', reference_contact)
                
                # Item 42: Declaration (no fields to save, just text)
                
                # Government Issued ID (below Item 42)
                government_issued_id = request.form.get('government_issued_id', '').strip() or None
                id_license_passport_no = request.form.get('id_license_passport_no', '').strip() or None
                date_place_of_issuance = request.form.get('date_place_of_issuance', '').strip() or None
                pds.government_issued_id = government_issued_id
                pds.id_license_passport_no = id_license_passport_no
                pds.date_place_of_issuance = date_place_of_issuance
                
                db.session.commit()
                flash('Other information saved successfully.', 'success')
            else:
                # Unknown section - don't commit, just redirect with error
                flash('Unknown section. No data was saved.', 'error')
            
            return redirect(url_for('routes.employee_pds', id=id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving personal information: {str(e)}', 'error')
    
    # Get user email if employee has a user account
    user_email = None
    if emp.user_id:
        user = User.query.get(emp.user_id)
        if user:
            user_email = user.email
    
    # Ensure name fields are synced from Employee table for display
    # Also sync address, mobile, phone, and email from Employee/User table if PDS exists
    if pds:
        pds.surname = emp.last_name
        pds.first_name = emp.first_name
        pds.middle_name = emp.middle_name
        # Sync phone, address, mobile, and email from Employee/User table (source of truth)
        pds.telephone_no = emp.phone if emp.phone else pds.telephone_no
        pds.mobile_no = emp.mobile_no if emp.mobile_no else pds.mobile_no
        pds.email_address = user_email if user_email else pds.email_address
        pds.residential_house_no = emp.residential_house_no if emp.residential_house_no else pds.residential_house_no
        pds.residential_street = emp.residential_street if emp.residential_street else pds.residential_street
        pds.residential_subdivision = emp.residential_subdivision if emp.residential_subdivision else pds.residential_subdivision
        pds.residential_barangay = emp.residential_barangay if emp.residential_barangay else pds.residential_barangay
        pds.residential_city = emp.residential_city if emp.residential_city else pds.residential_city
        pds.residential_province = emp.residential_province if emp.residential_province else pds.residential_province
        pds.residential_zip_code = emp.residential_zip_code if emp.residential_zip_code else pds.residential_zip_code
        db.session.commit()
    else:
        # Create PDS record if it doesn't exist, syncing from Employee/User table
        pds = EmployeePDS(
            employee_id=id,
            surname=emp.last_name,
            first_name=emp.first_name,
            middle_name=emp.middle_name,
            telephone_no=emp.phone,
            mobile_no=emp.mobile_no,
            email_address=user_email,
            residential_house_no=emp.residential_house_no,
            residential_street=emp.residential_street,
            residential_subdivision=emp.residential_subdivision,
            residential_barangay=emp.residential_barangay,
            residential_city=emp.residential_city,
            residential_province=emp.residential_province,
            residential_zip_code=emp.residential_zip_code
        )
        db.session.add(pds)
        db.session.commit()
        db.session.refresh(pds)
    
    return render_template(
        'employees/pds.html',
        emp=emp,
        pds=pds,
        employee=employee,
        can_edit_pds=can_edit_pds,
    )

# User Management Routes
@bp.route('/users')
@login_required
def users_list():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    from app.user_provision_service import count_active_employees_without_user
    users = (
        User.query
        .options(db.joinedload(User.employee))
        .order_by(User.created_at.desc())
        .all()
    )
    employees = Employee.query.filter_by(user_id=None).order_by(Employee.employee_id).all()
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'users/list.html',
        users=users,
        employees=employees,
        employee=employee,
        active_without_user_count=count_active_employees_without_user(),
    )


@bp.route('/users/provision-active', methods=['POST'])
@login_required
def users_provision_active():
    """Create user accounts for all active employees missing one."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    from app.user_provision_service import provision_users_for_active_employees

    result = provision_users_for_active_employees()
    if not result.get('ok'):
        flash(result.get('errors', ['Provisioning failed.'])[0], 'error')
        return redirect(url_for('routes.users_list'))

    parts = []
    if result.get('created'):
        parts.append(f"{result['created']} user account(s) created")
    if result.get('linked'):
        parts.append(f"{result['linked']} employee(s) linked to existing account(s)")
    if result.get('skipped'):
        parts.append(f"{result['skipped']} skipped (already have accounts)")
    flash(
        'Provisioning complete: ' + (', '.join(parts) if parts else 'no changes needed') + '. '
        'Initial password for new accounts is the employee ID.',
        'success',
    )
    if result.get('errors'):
        flash('Some rows were skipped: ' + '; '.join(result['errors'][:3]), 'info')
    return redirect(url_for('routes.users_list'))

@bp.route('/users/add', methods=['GET', 'POST'])
@login_required
def user_add():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            employee_id = request.form.get('employee_id', '').strip()
            role = request.form.get('role', 'employee')
            is_active = request.form.get('is_active') == 'on'
            
            # Check if username already exists
            if User.query.filter_by(username=username).first():
                flash('Username already exists.', 'error')
                employees = Employee.query.filter_by(user_id=None).order_by(Employee.employee_id).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', employees=employees, employee=employee)
            
            # Check if email already exists
            if User.query.filter_by(email=email).first():
                flash('Email already exists.', 'error')
                employees = Employee.query.filter_by(user_id=None).order_by(Employee.employee_id).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', employees=employees, employee=employee)
            
            # Check if employee_id already exists
            if User.query.filter_by(employee_id=employee_id).first():
                flash('Employee ID already has a user account.', 'error')
                employees = Employee.query.filter_by(user_id=None).order_by(Employee.employee_id).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', employees=employees, employee=employee)
            
            # Create user
            new_user = User(
                username=username,
                email=email,
                employee_id=employee_id,
                role=role,
                is_active=is_active
            )
            new_user.set_password(password)
            
            db.session.add(new_user)
            db.session.flush()  # Get the user ID without committing
            
            # Link to employee if employee_id matches
            emp = Employee.query.filter_by(employee_id=employee_id).first()
            if emp:
                emp.user_id = new_user.id
            
            db.session.commit()
            flash('User added successfully.', 'success')
            return redirect(url_for('routes.users_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding user: {str(e)}', 'error')
            employees = Employee.query.filter_by(user_id=None).order_by(Employee.employee_id).all()
            employee = current_user.employee if current_user.employee else None
            return render_template('users/form.html', employees=employees, employee=employee)
    
    employees = Employee.query.filter_by(user_id=None).order_by(Employee.employee_id).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('users/form.html', employees=employees, employee=employee)

@bp.route('/users/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def user_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    user = User.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '').strip()
            employee_id = request.form.get('employee_id', '').strip()
            role = request.form.get('role', 'employee')
            is_active = request.form.get('is_active') == 'on'
            
            # Check if username already exists (excluding current user)
            existing = User.query.filter_by(username=username).first()
            if existing and existing.id != id:
                flash('Username already exists.', 'error')
                employees = Employee.query.order_by(Employee.employee_id).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', user=user, employees=employees, employee=employee)
            
            # Check if email already exists (excluding current user)
            existing = User.query.filter_by(email=email).first()
            if existing and existing.id != id:
                flash('Email already exists.', 'error')
                employees = Employee.query.order_by(Employee.employee_id).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', user=user, employees=employees, employee=employee)
            
            # Check if employee_id already exists (excluding current user)
            existing = User.query.filter_by(employee_id=employee_id).first()
            if existing and existing.id != id:
                flash('Employee ID already has a user account.', 'error')
                employees = Employee.query.order_by(Employee.employee_id).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('users/form.html', user=user, employees=employees, employee=employee)
            
            # Update user
            user.username = username
            user.email = email
            user.employee_id = employee_id
            user.role = role
            user.is_active = is_active
            
            # Update password if provided
            if password:
                user.set_password(password)
            
            # Link to employee if employee_id matches
            emp = Employee.query.filter_by(employee_id=employee_id).first()
            if emp:
                emp.user_id = user.id
            
            db.session.commit()
            flash('User updated successfully.', 'success')
            return redirect(url_for('routes.users_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating user: {str(e)}', 'error')
            employees = Employee.query.order_by(Employee.employee_id).all()
            employee = current_user.employee if current_user.employee else None
            return render_template('users/form.html', user=user, employees=employees, employee=employee)
    
    employees = Employee.query.order_by(Employee.employee_id).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('users/form.html', user=user, employees=employees, employee=employee)

@bp.route('/users/delete/<int:id>', methods=['POST'])
@login_required
def user_delete(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    user = User.query.get_or_404(id)
    
    # Prevent deleting yourself
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('routes.users_list'))
    
    try:
        # Unlink employee if exists (keep PDS/employee row)
        if user.employee:
            user.employee.user_id = None

        # Notifications.user_id is NOT NULL — remove rows instead of nulling the FK.
        HrmsNotification.query.filter_by(user_id=user.id).delete(synchronize_session=False)

        db.session.delete(user)
        db.session.commit()
        flash('User deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting user: {str(e)}', 'error')
    
    return redirect(url_for('routes.users_list'))

# Department Management Routes
@bp.route('/departments')
@login_required
def departments_list():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    departments = Department.query.order_by(Department.created_at.desc()).all()
    employees = Employee.query.order_by(Employee.last_name, Employee.first_name, Employee.middle_name).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('departments/list.html', departments=departments, employees=employees, employee=employee)

@bp.route('/departments/add', methods=['GET', 'POST'])
@login_required
def department_add():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            manager_id = request.form.get('manager_id')
            
            # Check if department name already exists
            if Department.query.filter_by(name=name).first():
                flash('Department name already exists.', 'error')
                employees = Employee.query.order_by(Employee.last_name, Employee.first_name, Employee.middle_name).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('departments/form.html', employees=employees, employee=employee)
            
            # Create department
            new_department = Department(
                name=name,
                description=description if description else None,
                manager_id=int(manager_id) if manager_id else None
            )
            
            db.session.add(new_department)
            db.session.commit()
            flash('Department added successfully.', 'success')
            return redirect(url_for('routes.departments_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding department: {str(e)}', 'error')
            employees = Employee.query.order_by(Employee.last_name, Employee.first_name, Employee.middle_name).all()
            employee = current_user.employee if current_user.employee else None
            return render_template('departments/form.html', employees=employees, employee=employee)
    
    employees = Employee.query.order_by(Employee.last_name, Employee.first_name, Employee.middle_name).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('departments/form.html', employees=employees, employee=employee)

@bp.route('/departments/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def department_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    dept = Department.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            manager_id = request.form.get('manager_id')
            
            # Check if department name already exists (excluding current department)
            existing = Department.query.filter_by(name=name).first()
            if existing and existing.id != id:
                flash('Department name already exists.', 'error')
                employees = Employee.query.order_by(Employee.last_name, Employee.first_name, Employee.middle_name).all()
                employee = current_user.employee if current_user.employee else None
                return render_template('departments/form.html', dept=dept, employees=employees, employee=employee)
            
            # Update department
            dept.name = name
            dept.description = description if description else None
            dept.manager_id = int(manager_id) if manager_id else None
            
            db.session.commit()
            flash('Department updated successfully.', 'success')
            return redirect(url_for('routes.departments_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating department: {str(e)}', 'error')
            employees = Employee.query.order_by(Employee.last_name, Employee.first_name, Employee.middle_name).all()
            employee = current_user.employee if current_user.employee else None
            return render_template('departments/form.html', dept=dept, employees=employees, employee=employee)
    
    employees = Employee.query.order_by(Employee.last_name, Employee.first_name, Employee.middle_name).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('departments/form.html', dept=dept, employees=employees, employee=employee)

@bp.route('/departments/delete/<int:id>', methods=['POST'])
@login_required
def department_delete(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    dept = Department.query.get_or_404(id)
    
    try:
        # Check if department has employees
        if dept.employees:
            flash('Cannot delete department with assigned employees. Please reassign employees first.', 'error')
            return redirect(url_for('routes.departments_list'))
        
        db.session.delete(dept)
        db.session.commit()
        flash('Department deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting department: {str(e)}', 'error')
    
    return redirect(url_for('routes.departments_list'))


# Position Management Routes
@bp.route('/positions')
@login_required
def positions_list():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    positions = Position.query.order_by(Position.title).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('positions/list.html', positions=positions, employee=employee)


@bp.route('/positions/add', methods=['GET', 'POST'])
@login_required
def position_add():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    if request.method == 'POST':
        try:
            title = request.form.get('title', '').strip()
            if not title:
                flash('Title is required.', 'error')
                employee = current_user.employee if current_user.employee else None
                return render_template('positions/form.html', employee=employee)
            if Position.query.filter_by(title=title).first():
                flash('Position title already exists.', 'error')
                employee = current_user.employee if current_user.employee else None
                return render_template('positions/form.html', employee=employee)
            new_position = Position(title=title)
            db.session.add(new_position)
            db.session.commit()
            flash('Position added successfully.', 'success')
            return redirect(url_for('routes.positions_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding position: {str(e)}', 'error')
            employee = current_user.employee if current_user.employee else None
            return render_template('positions/form.html', employee=employee)
    employee = current_user.employee if current_user.employee else None
    return render_template('positions/form.html', employee=employee)


@bp.route('/positions/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def position_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    pos = Position.query.get_or_404(id)
    if request.method == 'POST':
        try:
            title = request.form.get('title', '').strip()
            if not title:
                flash('Title is required.', 'error')
                employee = current_user.employee if current_user.employee else None
                return render_template('positions/form.html', position=pos, employee=employee)
            existing = Position.query.filter_by(title=title).first()
            if existing and existing.id != id:
                flash('Position title already exists.', 'error')
                employee = current_user.employee if current_user.employee else None
                return render_template('positions/form.html', position=pos, employee=employee)
            pos.title = title
            db.session.commit()
            flash('Position updated successfully.', 'success')
            return redirect(url_for('routes.positions_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating position: {str(e)}', 'error')
            employee = current_user.employee if current_user.employee else None
            return render_template('positions/form.html', position=pos, employee=employee)
    employee = current_user.employee if current_user.employee else None
    return render_template('positions/form.html', position=pos, employee=employee)


@bp.route('/positions/delete/<int:id>', methods=['POST'])
@login_required
def position_delete(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    pos = Position.query.get_or_404(id)
    try:
        db.session.delete(pos)
        db.session.commit()
        flash('Position deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting position: {str(e)}', 'error')
    return redirect(url_for('routes.positions_list'))


@bp.route('/jo-cos-designations')
@login_required
def jo_cos_designations_list():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    rows = JoCosDesignation.query.order_by(JoCosDesignation.sort_order, JoCosDesignation.id).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('jo_cos_designations/list.html', designations=rows, employee=employee)


@bp.route('/jo-cos-designations/add', methods=['GET', 'POST'])
@login_required
def jo_cos_designation_add():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    if request.method == 'POST':
        des = _normalize_jo_cos_designation_form(request.form.get('designation'))
        if not des:
            flash('Designation is required.', 'error')
            return render_template('jo_cos_designations/form.html', designation=None, employee=employee)
        if len(des) > 500:
            flash('Designation must be at most 500 characters.', 'error')
            return render_template('jo_cos_designations/form.html', designation=None, employee=employee)
        sort_raw = (request.form.get('sort_order') or '').strip()
        try:
            sort_order = int(sort_raw) if sort_raw else _next_jo_cos_designation_sort_order()
            if sort_order < 0:
                raise ValueError('negative')
        except ValueError:
            flash('Sort order must be a non-negative integer.', 'error')
            return render_template('jo_cos_designations/form.html', designation=None, employee=employee)
        row = JoCosDesignation(designation=des, sort_order=sort_order)
        db.session.add(row)
        try:
            db.session.commit()
            flash('Designation added successfully.', 'success')
            return redirect(url_for('routes.jo_cos_designations_list'))
        except IntegrityError:
            db.session.rollback()
            flash('That designation already exists.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding designation: {str(e)}', 'error')
        return render_template('jo_cos_designations/form.html', designation=None, employee=employee)
    return render_template('jo_cos_designations/form.html', designation=None, employee=employee)


@bp.route('/jo-cos-designations/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def jo_cos_designation_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    row = JoCosDesignation.query.get_or_404(id)
    employee = current_user.employee if current_user.employee else None
    if request.method == 'POST':
        des = _normalize_jo_cos_designation_form(request.form.get('designation'))
        if not des:
            flash('Designation is required.', 'error')
            return render_template('jo_cos_designations/form.html', designation=row, employee=employee)
        if len(des) > 500:
            flash('Designation must be at most 500 characters.', 'error')
            return render_template('jo_cos_designations/form.html', designation=row, employee=employee)
        sort_raw = (request.form.get('sort_order') or '').strip()
        try:
            sort_order = int(sort_raw) if sort_raw else row.sort_order
            if sort_order < 0:
                raise ValueError('negative')
        except ValueError:
            flash('Sort order must be a non-negative integer.', 'error')
            return render_template('jo_cos_designations/form.html', designation=row, employee=employee)
        existing = JoCosDesignation.query.filter(JoCosDesignation.designation == des, JoCosDesignation.id != id).first()
        if existing:
            flash('That designation already exists.', 'error')
            return render_template('jo_cos_designations/form.html', designation=row, employee=employee)
        row.designation = des
        row.sort_order = sort_order
        try:
            db.session.commit()
            flash('Designation updated successfully.', 'success')
            return redirect(url_for('routes.jo_cos_designations_list'))
        except IntegrityError:
            db.session.rollback()
            flash('That designation already exists.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating designation: {str(e)}', 'error')
        return render_template('jo_cos_designations/form.html', designation=row, employee=employee)
    return render_template('jo_cos_designations/form.html', designation=row, employee=employee)


@bp.route('/jo-cos-designations/delete/<int:id>', methods=['POST'])
@login_required
def jo_cos_designation_delete(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    row = JoCosDesignation.query.get_or_404(id)
    try:
        db.session.delete(row)
        db.session.commit()
        flash('Designation deleted. Employees using it had their JO/COS position link cleared.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting designation: {str(e)}', 'error')
    return redirect(url_for('routes.jo_cos_designations_list'))


JO_COS_RATE_STATUSES = ('Job Order', 'Contract of Service')


def _normalize_jo_cos_rate_label(text):
    if text is None:
        return None
    s = str(text).replace('\r\n', '\n').replace('\r', '\n').strip()
    if not s or s.lower() == 'nan':
        return None
    return ' '.join(s.split())


def _parse_jo_cos_rate_decimal(raw):
    if raw is None or str(raw).strip() == '':
        return None
    try:
        return Decimal(str(raw).strip().replace(',', ''))
    except Exception:
        return None


def _jo_cos_rates_ordered():
    return JoCosRate.query.order_by(JoCosRate.sort_order, JoCosRate.status_of_appointment, JoCosRate.designation_label).all()


@bp.route('/jo-cos-rates')
@login_required
def jo_cos_rates_list():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    rows = _jo_cos_rates_ordered()
    employee = current_user.employee if current_user.employee else None
    return render_template('jo_cos_rates/list.html', rates=rows, employee=employee)


@bp.route('/jo-cos-rates/add', methods=['GET', 'POST'])
@login_required
def jo_cos_rate_add():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    if request.method == 'POST':
        status = (request.form.get('status_of_appointment') or '').strip()
        label = _normalize_jo_cos_rate_label(request.form.get('designation_label'))
        rate = _parse_jo_cos_rate_decimal(request.form.get('rate_per_day'))
        sort_raw = (request.form.get('sort_order') or '').strip()
        if status not in JO_COS_RATE_STATUSES:
            flash('Status of appointment must be Job Order or Contract of Service.', 'error')
            return render_template('jo_cos_rates/form.html', rate=None, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        if not label:
            flash('Designation is required.', 'error')
            return render_template('jo_cos_rates/form.html', rate=None, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        if len(label) > 500:
            flash('Designation must be at most 500 characters.', 'error')
            return render_template('jo_cos_rates/form.html', rate=None, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        if rate is None or rate <= 0:
            flash('Rate per day must be a positive number.', 'error')
            return render_template('jo_cos_rates/form.html', rate=None, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        try:
            sort_order = int(sort_raw) if sort_raw else (db.session.query(func.max(JoCosRate.sort_order)).scalar() or 0) + 1
            if sort_order < 0:
                raise ValueError('negative')
        except ValueError:
            flash('Sort order must be a non-negative integer.', 'error')
            return render_template('jo_cos_rates/form.html', rate=None, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        row = JoCosRate(
            status_of_appointment=status,
            designation_label=label,
            rate_per_day=rate,
            sort_order=sort_order,
        )
        db.session.add(row)
        try:
            db.session.commit()
            flash('Rate added successfully.', 'success')
            return redirect(url_for('routes.jo_cos_rates_list'))
        except IntegrityError:
            db.session.rollback()
            flash('A rate for this status and designation already exists.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding rate: {str(e)}', 'error')
        return render_template('jo_cos_rates/form.html', rate=None, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
    return render_template('jo_cos_rates/form.html', rate=None, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)


@bp.route('/jo-cos-rates/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def jo_cos_rate_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    row = JoCosRate.query.get_or_404(id)
    employee = current_user.employee if current_user.employee else None
    if request.method == 'POST':
        status = (request.form.get('status_of_appointment') or '').strip()
        label = _normalize_jo_cos_rate_label(request.form.get('designation_label'))
        rate = _parse_jo_cos_rate_decimal(request.form.get('rate_per_day'))
        sort_raw = (request.form.get('sort_order') or '').strip()
        if status not in JO_COS_RATE_STATUSES:
            flash('Status of appointment must be Job Order or Contract of Service.', 'error')
            return render_template('jo_cos_rates/form.html', rate=row, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        if not label:
            flash('Designation is required.', 'error')
            return render_template('jo_cos_rates/form.html', rate=row, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        if len(label) > 500:
            flash('Designation must be at most 500 characters.', 'error')
            return render_template('jo_cos_rates/form.html', rate=row, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        if rate is None or rate <= 0:
            flash('Rate per day must be a positive number.', 'error')
            return render_template('jo_cos_rates/form.html', rate=row, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        try:
            sort_order = int(sort_raw) if sort_raw else row.sort_order
            if sort_order < 0:
                raise ValueError('negative')
        except ValueError:
            flash('Sort order must be a non-negative integer.', 'error')
            return render_template('jo_cos_rates/form.html', rate=row, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        dup = JoCosRate.query.filter(
            JoCosRate.status_of_appointment == status,
            JoCosRate.designation_label == label,
            JoCosRate.id != id,
        ).first()
        if dup:
            flash('A rate for this status and designation already exists.', 'error')
            return render_template('jo_cos_rates/form.html', rate=row, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
        row.status_of_appointment = status
        row.designation_label = label
        row.rate_per_day = rate
        row.sort_order = sort_order
        try:
            db.session.commit()
            flash('Rate updated successfully.', 'success')
            return redirect(url_for('routes.jo_cos_rates_list'))
        except IntegrityError:
            db.session.rollback()
            flash('A rate for this status and designation already exists.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating rate: {str(e)}', 'error')
        return render_template('jo_cos_rates/form.html', rate=row, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)
    return render_template('jo_cos_rates/form.html', rate=row, employee=employee, jo_cos_rate_statuses=JO_COS_RATE_STATUSES)


@bp.route('/jo-cos-rates/delete/<int:id>', methods=['POST'])
@login_required
def jo_cos_rate_delete(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    row = JoCosRate.query.get_or_404(id)
    try:
        db.session.delete(row)
        db.session.commit()
        flash('Rate deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting rate: {str(e)}', 'error')
    return redirect(url_for('routes.jo_cos_rates_list'))


def _normalize_flexi_shift_code(text):
    if text is None:
        return None
    s = str(text).replace('\r\n', '\n').replace('\r', '\n').strip()
    if not s or s.lower() == 'nan':
        return None
    return ' '.join(s.split())


def _normalize_flexi_time_display(text):
    if text is None:
        return None
    s = str(text).replace('\r\n', '\n').replace('\r', '\n').strip()
    if not s or s.lower() == 'nan':
        return None
    return ' '.join(s.split())


def _flexi_time_schedules_ordered():
    return FlexiTimeSchedule.query.order_by(FlexiTimeSchedule.sort_order, FlexiTimeSchedule.shift_code).all()


@bp.route('/flexi-time-schedule')
@login_required
def flexi_time_schedule_list():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    rows = _flexi_time_schedules_ordered()
    employee = current_user.employee if current_user.employee else None
    return render_template('flexi_time_schedule/list.html', schedules=rows, employee=employee)


@bp.route('/flexi-time-schedule/add', methods=['GET', 'POST'])
@login_required
def flexi_time_schedule_add():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employee = current_user.employee if current_user.employee else None
    if request.method == 'POST':
        code = _normalize_flexi_shift_code(request.form.get('shift_code'))
        tin = _normalize_flexi_time_display(request.form.get('time_in'))
        tout = _normalize_flexi_time_display(request.form.get('time_out'))
        sort_raw = (request.form.get('sort_order') or '').strip()
        if not code:
            flash('Shift code is required.', 'error')
            return render_template('flexi_time_schedule/form.html', row=None, employee=employee)
        if len(code) > 64:
            flash('Shift code must be at most 64 characters.', 'error')
            return render_template('flexi_time_schedule/form.html', row=None, employee=employee)
        if not tin or not tout:
            flash('Time in and time out are required.', 'error')
            return render_template('flexi_time_schedule/form.html', row=None, employee=employee)
        if len(tin) > 64 or len(tout) > 64:
            flash('Time in/out must be at most 64 characters each.', 'error')
            return render_template('flexi_time_schedule/form.html', row=None, employee=employee)
        try:
            sort_order = int(sort_raw) if sort_raw else (db.session.query(func.max(FlexiTimeSchedule.sort_order)).scalar() or 0) + 1
            if sort_order < 0:
                raise ValueError('negative')
        except ValueError:
            flash('Sort order must be a non-negative integer.', 'error')
            return render_template('flexi_time_schedule/form.html', row=None, employee=employee)
        row = FlexiTimeSchedule(shift_code=code, time_in=tin, time_out=tout, sort_order=sort_order)
        db.session.add(row)
        try:
            db.session.commit()
            flash('Flexi-time schedule added.', 'success')
            return redirect(url_for('routes.flexi_time_schedule_list'))
        except IntegrityError:
            db.session.rollback()
            flash('A schedule with this shift code already exists.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding schedule: {str(e)}', 'error')
        return render_template('flexi_time_schedule/form.html', row=None, employee=employee)
    return render_template('flexi_time_schedule/form.html', row=None, employee=employee)


@bp.route('/flexi-time-schedule/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def flexi_time_schedule_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    row = FlexiTimeSchedule.query.get_or_404(id)
    employee = current_user.employee if current_user.employee else None
    if request.method == 'POST':
        code = _normalize_flexi_shift_code(request.form.get('shift_code'))
        tin = _normalize_flexi_time_display(request.form.get('time_in'))
        tout = _normalize_flexi_time_display(request.form.get('time_out'))
        sort_raw = (request.form.get('sort_order') or '').strip()
        if not code:
            flash('Shift code is required.', 'error')
            return render_template('flexi_time_schedule/form.html', row=row, employee=employee)
        if len(code) > 64:
            flash('Shift code must be at most 64 characters.', 'error')
            return render_template('flexi_time_schedule/form.html', row=row, employee=employee)
        if not tin or not tout:
            flash('Time in and time out are required.', 'error')
            return render_template('flexi_time_schedule/form.html', row=row, employee=employee)
        if len(tin) > 64 or len(tout) > 64:
            flash('Time in/out must be at most 64 characters each.', 'error')
            return render_template('flexi_time_schedule/form.html', row=row, employee=employee)
        try:
            sort_order = int(sort_raw) if sort_raw else row.sort_order
            if sort_order < 0:
                raise ValueError('negative')
        except ValueError:
            flash('Sort order must be a non-negative integer.', 'error')
            return render_template('flexi_time_schedule/form.html', row=row, employee=employee)
        dup = FlexiTimeSchedule.query.filter(
            FlexiTimeSchedule.shift_code == code,
            FlexiTimeSchedule.id != id,
        ).first()
        if dup:
            flash('A schedule with this shift code already exists.', 'error')
            return render_template('flexi_time_schedule/form.html', row=row, employee=employee)
        row.shift_code = code
        row.time_in = tin
        row.time_out = tout
        row.sort_order = sort_order
        try:
            db.session.commit()
            flash('Flexi-time schedule updated.', 'success')
            return redirect(url_for('routes.flexi_time_schedule_list'))
        except IntegrityError:
            db.session.rollback()
            flash('A schedule with this shift code already exists.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating schedule: {str(e)}', 'error')
        return render_template('flexi_time_schedule/form.html', row=row, employee=employee)
    return render_template('flexi_time_schedule/form.html', row=row, employee=employee)


@bp.route('/flexi-time-schedule/delete/<int:id>', methods=['POST'])
@login_required
def flexi_time_schedule_delete(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    row = FlexiTimeSchedule.query.get_or_404(id)
    try:
        db.session.delete(row)
        db.session.commit()
        flash('Flexi-time schedule deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting schedule: {str(e)}', 'error')
    return redirect(url_for('routes.flexi_time_schedule_list'))


def _current_salary_amount(emp):
    """Look up current salary amount from SalaryGrade for an employee. Returns float or None."""
    if not emp or not emp.salary_grade or not emp.salary_step or emp.salary_step < 1 or emp.salary_step > 8:
        return None
    from sqlalchemy import func as sql_func
    row = SalaryGrade.query.filter(
        SalaryGrade.sg == emp.salary_grade,
        sql_func.coalesce(SalaryGrade.sg_agency, '') == (emp.agency or ''),
        sql_func.coalesce(SalaryGrade.sg_lgu_class, '') == (emp.lgu_class_level or ''),
        sql_func.coalesce(SalaryGrade.sg_tranche, '') == (emp.salary_tranche or '')
    ).first()
    if not row:
        return None
    val = getattr(row, f'sg_step_{emp.salary_step}', None)
    return float(val) if val is not None else None


def _add_years(d, years):
    """Add years to a date; handle Feb 29 -> non-leap by returning Feb 28."""
    if d is None:
        return None
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 2, 28)


STEP_INCREMENT_YEARS = 3
STEP_INCREMENT_NOTIFY_DAYS = 15
MAX_SALARY_STEP = 8
APPOINTMENT_CHANGE_ORIGINAL = 'Original Appointment'
APPOINTMENT_CHANGE_CHANGE = 'Appointment Change'
APPOINTMENT_CHANGE_STEP_INCREMENT = 'Step Increment'
APPOINTMENT_CHANGE_UPDATE = 'Appointment Update'
APPOINTMENT_CHANGE_HISTORY_ENTRY = 'History Entry'

APPOINTMENT_STATUS_OPTIONS = (
    'Permanent', 'Casual', 'Contractual', 'Temporary', 'Coterminous',
    'Job Order', 'Contract of Service',
)
APPOINTMENT_NATURE_OPTIONS = (
    'Original', 'Reemployment', 'Reappointment', 'Transfer', 'Promotion', 'Demotion',
)


def _salary_amount_for_grade_step(emp, sal_grade, sal_step):
    """Salary amount for a specific SG/step using the employee's agency/LGU/tranche."""
    if not emp or not sal_grade or not sal_step or sal_step < 1 or sal_step > MAX_SALARY_STEP:
        return None
    from sqlalchemy import func as sql_func
    row = SalaryGrade.query.filter(
        SalaryGrade.sg == sal_grade,
        sql_func.coalesce(SalaryGrade.sg_agency, '') == (emp.agency or ''),
        sql_func.coalesce(SalaryGrade.sg_lgu_class, '') == (emp.lgu_class_level or ''),
        sql_func.coalesce(SalaryGrade.sg_tranche, '') == (emp.salary_tranche or ''),
    ).first()
    if not row:
        return None
    val = getattr(row, f'sg_step_{sal_step}', None)
    return val


def _is_plantilla_for_appointment_history(emp) -> bool:
    """Plantilla (non-JO/COS) employees participate in appointment/SI history."""
    return bool(emp) and not _is_jo_cos_employee(emp)


def _build_appointment_history_entry(
    emp,
    *,
    change_type,
    effectivity_date,
    appoint_status=None,
    appoint_nature=None,
    sal_grade=None,
    sal_step=None,
    from_sal_step=None,
    to_sal_step=None,
    remarks=None,
    appoint_date=None,
):
    """Build an EmployeeAppointmentHistory row (not yet added to session)."""
    status = appoint_status if appoint_status is not None else emp.status_of_appointment
    nature = appoint_nature if appoint_nature is not None else emp.nature_of_appointment
    grade = sal_grade if sal_grade is not None else emp.salary_grade
    step = sal_step if sal_step is not None else emp.salary_step
    sal_amount = _salary_amount_for_grade_step(emp, grade, step) if grade and step else None
    return EmployeeAppointmentHistory(
        emp_id=emp.id,
        emp_dept=emp.department.name if emp.department else None,
        emp_position=emp.position,
        appoint_date=appoint_date if appoint_date is not None else emp.appointment_date,
        appoint_status=status,
        appoint_nature=nature,
        agency=emp.agency,
        lgu_class=emp.lgu_class_level,
        sal_grade=grade,
        sal_step=step,
        sal_amount=sal_amount,
        change_type=change_type,
        effectivity_date=effectivity_date,
        from_sal_step=from_sal_step,
        to_sal_step=to_sal_step if to_sal_step is not None else step,
        remarks=remarks,
        location=request.remote_addr if request else None,
        user_id=current_user.id if current_user and getattr(current_user, 'is_authenticated', False) else None,
    )


def _seed_opening_appointment_history(emp, *, force=False):
    """
    Insert Original Appointment from current employee fields when none exists.
    Returns True if a row was inserted.
    """
    if not emp or not _is_plantilla_for_appointment_history(emp):
        return False
    if not force and emp.appointment_history.count() > 0:
        return False
    effectivity = emp.appointment_date or date.today()
    entry = _build_appointment_history_entry(
        emp,
        change_type=APPOINTMENT_CHANGE_ORIGINAL,
        effectivity_date=effectivity,
        appoint_date=emp.appointment_date,
        from_sal_step=None,
        to_sal_step=emp.salary_step,
        remarks='Opening appointment (seeded)',
    )
    db.session.add(entry)
    return True


def _step_increment_eligible(emp) -> bool:
    """Plantilla employees below step 8 with an appointment date can earn SI."""
    if not emp or (emp.status or '').strip().lower() not in ('active', 'inactive'):
        return False
    if _is_jo_cos_employee(emp):
        return False
    if not emp.appointment_date:
        return False
    if emp.salary_step is None or emp.salary_step < 1 or emp.salary_step >= MAX_SALARY_STEP:
        return False
    if not emp.salary_grade:
        return False
    return True


def _step_increment_base_date(emp, history_rows=None):
    """
    Base date for next SI due:
    last Step Increment / step-raising history effectivity, else original appointment_date.
    """
    if history_rows is None:
        history_rows = list(
            emp.appointment_history.order_by(
                EmployeeAppointmentHistory.timestamp.desc(),
                EmployeeAppointmentHistory.id.desc(),
            ).all()
        )
    for h in history_rows:
        ctype = (h.change_type or '').strip()
        nature = (h.appoint_nature or '').strip()
        step_raised = (
            h.from_sal_step is not None
            and h.to_sal_step is not None
            and h.to_sal_step > h.from_sal_step
        )
        if (
            ctype == APPOINTMENT_CHANGE_STEP_INCREMENT
            or nature == APPOINTMENT_CHANGE_STEP_INCREMENT
            or step_raised
        ):
            return h.effectivity_date or h.appoint_date
    return emp.appointment_date


def _step_increment_due_date(emp, history_rows=None):
    """Next SI due date, or None if not eligible."""
    if not _step_increment_eligible(emp):
        return None
    base = _step_increment_base_date(emp, history_rows=history_rows)
    return _add_years(base, STEP_INCREMENT_YEARS)


def _step_increment_due_employees(employees, notify_days=STEP_INCREMENT_NOTIFY_DAYS):
    """
    Employees with SI due on or before (today + notify_days).
    Includes overdue (not yet recorded) and upcoming within the notify window.
    Returns list of (emp, due_date, overdue: bool).
    """
    today = date.today()
    window_end = today + timedelta(days=notify_days)
    out = []
    for emp in employees:
        history = list(
            emp.appointment_history.order_by(
                EmployeeAppointmentHistory.timestamp.desc(),
                EmployeeAppointmentHistory.id.desc(),
            ).all()
        )
        due = _step_increment_due_date(emp, history_rows=history)
        if due is None:
            continue
        if due <= window_end:
            out.append((emp, due, due < today))
    out.sort(key=lambda x: (x[1], (x[0].last_name or ''), (x[0].first_name or '')))
    return out


@bp.route('/status-of-appointment')
@login_required
def status_of_appointment_list():
    """Status of Appointment page: employees list with expandable current appointment and history."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    employees = Employee.query.options(db.joinedload(Employee.department)).order_by(
        Employee.last_name, Employee.first_name
    ).all()
    due_list = _step_increment_due_employees(employees)
    step_increment_due_count = len(due_list)
    step_increment_due_list = [(emp, due, overdue) for emp, due, overdue in due_list]
    plantilla_no_history = 0
    employees_with_amount = []
    for emp in employees:
        amount = _current_salary_amount(emp)
        history = list(
            emp.appointment_history.order_by(
                EmployeeAppointmentHistory.effectivity_date.desc().nullslast(),
                EmployeeAppointmentHistory.timestamp.desc(),
                EmployeeAppointmentHistory.id.desc(),
            ).all()
        )
        si_due = _step_increment_due_date(emp, history_rows=history)
        can_si = _step_increment_eligible(emp)
        is_plantilla = _is_plantilla_for_appointment_history(emp)
        if is_plantilla and not history:
            plantilla_no_history += 1
        employees_with_amount.append((emp, amount, history, si_due, can_si, is_plantilla))
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'status_of_appointment/list.html',
        employees_with_amount=employees_with_amount,
        employee=employee,
        step_increment_due_count=step_increment_due_count,
        step_increment_due_list=step_increment_due_list,
        step_increment_years=STEP_INCREMENT_YEARS,
        max_salary_step=MAX_SALARY_STEP,
        today=date.today().isoformat(),
        plantilla_no_history=plantilla_no_history,
        can_edit_original_appointment=_can_edit_employee_id(),
        appointment_status_options=APPOINTMENT_STATUS_OPTIONS,
        appointment_nature_options=APPOINTMENT_NATURE_OPTIONS,
        appointment_change_types=(
            APPOINTMENT_CHANGE_ORIGINAL,
            APPOINTMENT_CHANGE_CHANGE,
            APPOINTMENT_CHANGE_STEP_INCREMENT,
            APPOINTMENT_CHANGE_HISTORY_ENTRY,
            APPOINTMENT_CHANGE_UPDATE,
        ),
    )


@bp.route('/status-of-appointment/backfill-opening', methods=['POST'])
@login_required
def status_of_appointment_backfill_opening():
    """
    Minimal backfill: for plantilla employees with zero history rows, seed an
    Original Appointment from their *current* employee fields.
    Does not invent prior Temporary→Permanent periods — use Add history entry for those.
    """
    denied = _require_admin_or_hr()
    if denied:
        return denied
    try:
        inserted = 0
        skipped = 0
        employees = Employee.query.options(db.joinedload(Employee.department)).all()
        for emp in employees:
            if not _is_plantilla_for_appointment_history(emp):
                continue
            if emp.appointment_history.count() > 0:
                skipped += 1
                continue
            if _seed_opening_appointment_history(emp):
                inserted += 1
        db.session.commit()
        flash(
            f'Opening history backfill complete: {inserted} seeded, '
            f'{skipped} already had history (unchanged).',
            'success',
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Error during opening history backfill: {str(e)}', 'error')
    return redirect(url_for('routes.status_of_appointment_list'))


@bp.route('/status-of-appointment/<int:id>/appointment-change', methods=['POST'])
@login_required
def status_of_appointment_appointment_change(id):
    """Record a later appointment change without overwriting original appointment date."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp = Employee.query.get_or_404(id)
    try:
        if not _is_plantilla_for_appointment_history(emp):
            raise ValueError('Appointment change recording is for plantilla employees only.')
        effectivity_raw = (request.form.get('effectivity_date') or '').strip()
        if not effectivity_raw:
            raise ValueError('Effectivity date is required.')
        effectivity_date = datetime.strptime(effectivity_raw, '%Y-%m-%d').date()
        new_status = (request.form.get('status_of_appointment') or '').strip() or None
        new_nature = (request.form.get('nature_of_appointment') or '').strip() or None
        grade_raw = (request.form.get('salary_grade') or '').strip()
        step_raw = (request.form.get('salary_step') or '').strip()
        new_grade = int(grade_raw) if grade_raw else emp.salary_grade
        new_step = int(step_raw) if step_raw else emp.salary_step
        if new_step is not None and (new_step < 1 or new_step > MAX_SALARY_STEP):
            raise ValueError(f'Salary step must be between 1 and {MAX_SALARY_STEP}.')
        remarks = (request.form.get('remarks') or '').strip() or None

        old_status = emp.status_of_appointment
        old_nature = emp.nature_of_appointment
        old_grade = emp.salary_grade
        old_step = emp.salary_step

        if (
            new_status == old_status
            and new_nature == old_nature
            and new_grade == old_grade
            and new_step == old_step
        ):
            raise ValueError('No appointment/salary fields changed.')

        emp.status_of_appointment = new_status
        emp.nature_of_appointment = new_nature
        emp.salary_grade = new_grade
        emp.salary_step = new_step
        emp.updated_at = datetime.utcnow()
        # Original appointment_date intentionally unchanged.

        db.session.add(_build_appointment_history_entry(
            emp,
            change_type=APPOINTMENT_CHANGE_CHANGE,
            effectivity_date=effectivity_date,
            from_sal_step=old_step,
            to_sal_step=new_step,
            remarks=remarks,
        ))
        db.session.commit()
        flash(
            f'Appointment change recorded for {emp.employee_id} '
            f'effective {effectivity_date.isoformat()}.',
            'success',
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Error recording appointment change: {str(e)}', 'error')
    return redirect(url_for('routes.status_of_appointment_list'))


@bp.route('/status-of-appointment/<int:id>/history-entry', methods=['POST'])
@login_required
def status_of_appointment_history_entry(id):
    """
    Add / backfill a history row. Optionally apply values to the current employee
    record, and optionally correct the original appointment date (Admin only).
    """
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp = Employee.query.get_or_404(id)
    try:
        if not _is_plantilla_for_appointment_history(emp):
            raise ValueError('History entries are for plantilla employees only.')
        effectivity_raw = (request.form.get('effectivity_date') or '').strip()
        if not effectivity_raw:
            raise ValueError('Effectivity date is required.')
        effectivity_date = datetime.strptime(effectivity_raw, '%Y-%m-%d').date()
        change_type = (request.form.get('change_type') or '').strip() or APPOINTMENT_CHANGE_HISTORY_ENTRY
        status = (request.form.get('status_of_appointment') or '').strip() or None
        nature = (request.form.get('nature_of_appointment') or '').strip() or None
        grade_raw = (request.form.get('salary_grade') or '').strip()
        step_raw = (request.form.get('salary_step') or '').strip()
        from_step_raw = (request.form.get('from_sal_step') or '').strip()
        grade = int(grade_raw) if grade_raw else None
        step = int(step_raw) if step_raw else None
        from_step = int(from_step_raw) if from_step_raw else None
        remarks = (request.form.get('remarks') or '').strip() or None
        apply_current = request.form.get('apply_to_current') == '1'
        set_original = request.form.get('set_as_original_appointment_date') == '1'

        if step is not None and (step < 1 or step > MAX_SALARY_STEP):
            raise ValueError(f'Salary step must be between 1 and {MAX_SALARY_STEP}.')

        if set_original:
            if not _can_edit_employee_id():
                raise ValueError('Only Admin can correct the original appointment date.')
            emp.appointment_date = effectivity_date

        db.session.add(_build_appointment_history_entry(
            emp,
            change_type=change_type,
            effectivity_date=effectivity_date,
            appoint_status=status if status is not None else emp.status_of_appointment,
            appoint_nature=nature if nature is not None else emp.nature_of_appointment,
            sal_grade=grade if grade is not None else emp.salary_grade,
            sal_step=step if step is not None else emp.salary_step,
            from_sal_step=from_step,
            to_sal_step=step if step is not None else emp.salary_step,
            remarks=remarks,
            appoint_date=emp.appointment_date,
        ))

        if apply_current:
            if status is not None:
                emp.status_of_appointment = status
            if nature is not None:
                emp.nature_of_appointment = nature
            if grade is not None:
                emp.salary_grade = grade
            if step is not None:
                emp.salary_step = step
            emp.updated_at = datetime.utcnow()

        db.session.commit()
        flash(f'History entry added for {emp.employee_id}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding history entry: {str(e)}', 'error')
    return redirect(url_for('routes.status_of_appointment_list'))


def _require_admin_only():
    """Admin / Portal Admin only. Returns redirect response or None."""
    if _can_edit_employee_id():
        return None
    flash('Access denied. Only Admin can edit or delete appointment history rows.', 'error')
    return redirect(url_for('routes.status_of_appointment_list'))


@bp.route('/status-of-appointment/history/<int:history_id>/edit', methods=['POST'])
@login_required
def status_of_appointment_history_edit(history_id):
    """Admin only: edit an existing employment/appointment history row."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    admin_denied = _require_admin_only()
    if admin_denied:
        return admin_denied

    row = EmployeeAppointmentHistory.query.get_or_404(history_id)
    emp = Employee.query.get_or_404(row.emp_id)
    try:
        effectivity_raw = (request.form.get('effectivity_date') or '').strip()
        if not effectivity_raw:
            raise ValueError('Effectivity date is required.')
        effectivity_date = datetime.strptime(effectivity_raw, '%Y-%m-%d').date()
        change_type = (request.form.get('change_type') or '').strip() or row.change_type
        status = (request.form.get('status_of_appointment') or '').strip() or None
        nature = (request.form.get('nature_of_appointment') or '').strip() or None
        grade_raw = (request.form.get('salary_grade') or '').strip()
        step_raw = (request.form.get('salary_step') or '').strip()
        from_step_raw = (request.form.get('from_sal_step') or '').strip()
        appoint_date_raw = (request.form.get('appoint_date') or '').strip()
        grade = int(grade_raw) if grade_raw else None
        step = int(step_raw) if step_raw else None
        from_step = int(from_step_raw) if from_step_raw else None
        remarks = (request.form.get('remarks') or '').strip() or None
        apply_current = request.form.get('apply_to_current') == '1'
        set_original = request.form.get('set_as_original_appointment_date') == '1'

        if step is not None and (step < 1 or step > MAX_SALARY_STEP):
            raise ValueError(f'Salary step must be between 1 and {MAX_SALARY_STEP}.')

        if appoint_date_raw:
            row.appoint_date = datetime.strptime(appoint_date_raw, '%Y-%m-%d').date()
        row.change_type = change_type
        row.effectivity_date = effectivity_date
        row.appoint_status = status
        row.appoint_nature = nature
        row.sal_grade = grade
        row.sal_step = step
        row.from_sal_step = from_step
        row.to_sal_step = step
        row.remarks = remarks
        row.sal_amount = _salary_amount_for_grade_step(emp, grade, step) if grade and step else None
        row.emp_dept = emp.department.name if emp.department else row.emp_dept
        row.emp_position = emp.position or row.emp_position
        row.agency = emp.agency
        row.lgu_class = emp.lgu_class_level

        if set_original and effectivity_date:
            emp.appointment_date = effectivity_date

        if apply_current:
            if status is not None:
                emp.status_of_appointment = status
            if nature is not None:
                emp.nature_of_appointment = nature
            if grade is not None:
                emp.salary_grade = grade
            if step is not None:
                emp.salary_step = step
            emp.updated_at = datetime.utcnow()

        db.session.commit()
        flash(f'History row #{history_id} updated for {emp.employee_id}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating history row: {str(e)}', 'error')
    return redirect(url_for('routes.status_of_appointment_list'))


@bp.route('/status-of-appointment/history/<int:history_id>/delete', methods=['POST'])
@login_required
def status_of_appointment_history_delete(history_id):
    """Admin only: delete an employment/appointment history row."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    admin_denied = _require_admin_only()
    if admin_denied:
        return admin_denied

    row = EmployeeAppointmentHistory.query.get_or_404(history_id)
    emp_label = None
    try:
        emp = Employee.query.get(row.emp_id)
        emp_label = emp.employee_id if emp else str(row.emp_id)
        db.session.delete(row)
        db.session.commit()
        flash(f'History row #{history_id} deleted for {emp_label}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting history row: {str(e)}', 'error')
    return redirect(url_for('routes.status_of_appointment_list'))


@bp.route('/status-of-appointment/<int:id>/step-increment', methods=['POST'])
@login_required
def status_of_appointment_step_increment(id):
    """Record a plantilla step increment and append employment history."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp = Employee.query.get_or_404(id)
    try:
        if not _step_increment_eligible(emp):
            raise ValueError(
                'Employee is not eligible for step increment '
                '(JO/COS, missing appointment/SG/step, or already at step 8).'
            )
        effectivity_raw = (request.form.get('effectivity_date') or '').strip()
        if not effectivity_raw:
            raise ValueError('Effectivity date is required.')
        effectivity_date = datetime.strptime(effectivity_raw, '%Y-%m-%d').date()
        from_step = int(emp.salary_step)
        to_step_raw = (request.form.get('to_sal_step') or '').strip()
        to_step = int(to_step_raw) if to_step_raw else from_step + 1
        if to_step <= from_step:
            raise ValueError('New step must be higher than the current step.')
        if to_step > MAX_SALARY_STEP:
            raise ValueError(f'Salary step cannot exceed {MAX_SALARY_STEP}.')
        remarks = (request.form.get('remarks') or '').strip() or None

        emp.salary_step = to_step
        emp.updated_at = datetime.utcnow()

        db.session.add(_build_appointment_history_entry(
            emp,
            change_type=APPOINTMENT_CHANGE_STEP_INCREMENT,
            effectivity_date=effectivity_date,
            appoint_nature=APPOINTMENT_CHANGE_STEP_INCREMENT,
            from_sal_step=from_step,
            to_sal_step=to_step,
            remarks=remarks,
        ))
        db.session.commit()
        flash(
            f'Step increment recorded for {emp.employee_id}: '
            f'step {from_step} → {to_step} effective {effectivity_date.isoformat()}.',
            'success',
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Error recording step increment: {str(e)}', 'error')
    return redirect(url_for('routes.status_of_appointment_list'))


# Salary Grade Management Routes
@bp.route('/salary-grades')
@login_required
def salary_grades_list():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    agency = request.args.get('agency', 'Local').strip() or 'Local'
    if agency not in ('Local', 'National'):
        agency = 'Local'
    grades = SalaryGrade.query.filter_by(sg_agency=agency).order_by(SalaryGrade.sg_lgu_class, SalaryGrade.sg_tranche, SalaryGrade.sg).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('salary_grades/list.html', salary_grades=grades, agency=agency, employee=employee)


@bp.route('/salary-grades/upload', methods=['POST'])
@login_required
def salary_grade_upload():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    import csv
    from decimal import Decimal
    from io import TextIOWrapper

    file = request.files.get('salary_grade_file')
    if not file or file.filename == '':
        flash('No file selected. Please choose a UTF-8 CSV file (e.g. salary_grade.csv).', 'error')
        return redirect(url_for('routes.salary_grades_list'))

    try:
        stream = TextIOWrapper(file.stream, encoding='utf-8')
        reader = csv.DictReader(stream)
        rows_added = 0
        rows_updated = 0
        for row in reader:
            row = {k.strip().lower().replace(' ', '_'): v for k, v in row.items() if k}
            sg_val = row.get('sg')
            if sg_val is None or str(sg_val).strip() == '':
                continue
            try:
                sg = int(float(sg_val))
            except (ValueError, TypeError):
                continue
            sg_agency = (row.get('sg_agency') or '').strip() or None
            sg_tranche = (row.get('sg_tranche') or '').strip() or None
            sg_lgu_class = (row.get('sg_lgu_class') or '').strip() or None
            steps = []
            for i in range(1, 9):
                v = row.get('sg_step_%d' % i) or row.get('sg_step%d' % i) or ''
                try:
                    steps.append(Decimal(str(v).strip()) if str(v).strip() else None)
                except Exception:
                    steps.append(None)
            from sqlalchemy import func
            existing = SalaryGrade.query.filter(
                SalaryGrade.sg == sg,
                func.coalesce(SalaryGrade.sg_agency, '') == (sg_agency or ''),
                func.coalesce(SalaryGrade.sg_tranche, '') == (sg_tranche or ''),
                func.coalesce(SalaryGrade.sg_lgu_class, '') == (sg_lgu_class or '')
            ).first()
            if existing:
                existing.sg_step_1, existing.sg_step_2, existing.sg_step_3, existing.sg_step_4 = steps[0], steps[1], steps[2], steps[3]
                existing.sg_step_5, existing.sg_step_6, existing.sg_step_7, existing.sg_step_8 = steps[4], steps[5], steps[6], steps[7]
                rows_updated += 1
            else:
                rec = SalaryGrade(
                    sg=sg, sg_agency=sg_agency, sg_tranche=sg_tranche, sg_lgu_class=sg_lgu_class,
                    sg_step_1=steps[0], sg_step_2=steps[1], sg_step_3=steps[2], sg_step_4=steps[3],
                    sg_step_5=steps[4], sg_step_6=steps[5], sg_step_7=steps[6], sg_step_8=steps[7]
                )
                db.session.add(rec)
                rows_added += 1
        db.session.commit()
        flash(f'Salary grade CSV imported: {rows_added} added, {rows_updated} updated.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error importing CSV: {str(e)}. Ensure file is UTF-8 and has columns: sg, sg_agency, sg_tranche, sg_lgu_class, sg_step_1..sg_step_8.', 'error')
    return redirect(url_for('routes.salary_grades_list'))


@bp.route('/salary-grades/<int:id>')
@login_required
def salary_grade_json(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    rec = SalaryGrade.query.get_or_404(id)
    return jsonify({
        'id': rec.id,
        'sg': rec.sg,
        'sg_agency': rec.sg_agency or '',
        'sg_tranche': rec.sg_tranche or '',
        'sg_lgu_class': rec.sg_lgu_class or '',
        'sg_step_1': float(rec.sg_step_1) if rec.sg_step_1 is not None else None,
        'sg_step_2': float(rec.sg_step_2) if rec.sg_step_2 is not None else None,
        'sg_step_3': float(rec.sg_step_3) if rec.sg_step_3 is not None else None,
        'sg_step_4': float(rec.sg_step_4) if rec.sg_step_4 is not None else None,
        'sg_step_5': float(rec.sg_step_5) if rec.sg_step_5 is not None else None,
        'sg_step_6': float(rec.sg_step_6) if rec.sg_step_6 is not None else None,
        'sg_step_7': float(rec.sg_step_7) if rec.sg_step_7 is not None else None,
        'sg_step_8': float(rec.sg_step_8) if rec.sg_step_8 is not None else None,
    })


@bp.route('/salary-grades/edit/<int:id>', methods=['POST'])
@login_required
def salary_grade_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    from decimal import Decimal
    rec = SalaryGrade.query.get_or_404(id)
    try:
        rec.sg = int(request.form.get('sg') or 0) or None
        rec.sg_agency = (request.form.get('sg_agency') or '').strip() or None
        rec.sg_tranche = (request.form.get('sg_tranche') or '').strip() or None
        rec.sg_lgu_class = (request.form.get('sg_lgu_class') or '').strip() or None
        for i in range(1, 9):
            v = request.form.get('sg_step_%d' % i)
            try:
                setattr(rec, 'sg_step_%d' % i, Decimal(v) if v and str(v).strip() else None)
            except Exception:
                setattr(rec, 'sg_step_%d' % i, None)
        db.session.commit()
        flash('Salary grade updated successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating salary grade: {str(e)}', 'error')
    return redirect(url_for('routes.salary_grades_list', agency=rec.sg_agency or 'Local'))


# Leave Settings (Leave Types / Leave Credits) - Admin & HR only
@bp.route('/leave-settings')
@login_required
def leave_settings_list():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    types = LeaveType.query.order_by(LeaveType.code).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('leave_settings/list.html', leave_types=types, employee=employee)


@bp.route('/leave-settings/add', methods=['GET', 'POST'])
@login_required
def leave_type_add():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    if request.method == 'POST':
        code = (request.form.get('code') or '').strip()
        name = (request.form.get('name') or '').strip()
        description = (request.form.get('description') or '').strip() or None
        is_active = request.form.get('is_active') == 'on'
        if not code or not name:
            flash('Code and Name are required.', 'error')
            return redirect(url_for('routes.leave_settings_list'))
        if LeaveType.query.filter_by(code=code).first():
            flash(f'Leave type with code "{code}" already exists.', 'error')
            return redirect(url_for('routes.leave_settings_list'))
        rec = LeaveType(code=code, name=name, description=description, is_active=is_active)
        db.session.add(rec)
        db.session.commit()
        flash('Leave type added successfully.', 'success')
        return redirect(url_for('routes.leave_settings_list'))
    return redirect(url_for('routes.leave_settings_list'))


@bp.route('/leave-settings/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def leave_type_edit(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    rec = LeaveType.query.get_or_404(id)
    if request.method == 'POST':
        code = (request.form.get('code') or '').strip()
        name = (request.form.get('name') or '').strip()
        description = (request.form.get('description') or '').strip() or None
        is_active = request.form.get('is_active') == 'on'
        if not code or not name:
            flash('Code and Name are required.', 'error')
            return redirect(url_for('routes.leave_settings_list'))
        other = LeaveType.query.filter(LeaveType.code == code, LeaveType.id != id).first()
        if other:
            flash(f'Another leave type with code "{code}" already exists.', 'error')
            return redirect(url_for('routes.leave_settings_list'))
        rec.code = code
        rec.name = name
        rec.description = description
        rec.is_active = is_active
        db.session.commit()
        flash('Leave type updated successfully.', 'success')
        return redirect(url_for('routes.leave_settings_list'))
    return redirect(url_for('routes.leave_settings_list'))


@bp.route('/leave-settings/delete/<int:id>', methods=['POST'])
@login_required
def leave_type_delete(id):
    denied = _require_admin_or_hr()
    if denied:
        return denied
    rec = LeaveType.query.get_or_404(id)
    try:
        db.session.delete(rec)
        db.session.commit()
        flash('Leave type deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Could not delete: {str(e)}', 'error')
    return redirect(url_for('routes.leave_settings_list'))
