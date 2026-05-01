from flask import Blueprint, render_template, request, redirect, url_for, flash, session, Response, jsonify, send_file, abort
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, date, timedelta
import calendar
import json
import csv
import io
import os
from app import db
from app.leave_ledger_service import (
    LEAVE_CREDITS_STATUSES,
    accrue_monthly_vl_sl_for_month,
    ensure_appointment_spl_wl_grant,
    record_leave_ledger_deletion,
    recompute_leave_ledger_balances,
    undo_monthly_accrual_for_month,
)
from app.models import User, Employee, Department, Position, SalaryGrade, Attendance, LeaveRequest, LeaveType, LeaveBalance, EmployeePDS, EmployeeAppointmentHistory, DailyTimeRecord, LeaveLedger, DtrWorkArrangementSetting, DtrJustification, PayrollSubmission
from app.dtr_parse import parse_dtr_dat_file as _parse_dtr_dat_file
from decimal import Decimal
from werkzeug.utils import secure_filename
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

bp = Blueprint('routes', __name__)

def _next_employee_id_6digit() -> str:
    """
    Returns the next 6-digit numeric employee_id, based on the current maximum
    6-digit numeric employee_id in the database.
    """
    from sqlalchemy import text

    # Postgres regex (~) matches only 6-digit numeric IDs (e.g. '000123').
    max_val = db.session.execute(
        text(
            r"SELECT MAX(CAST(employee_id AS INTEGER)) "
            r"FROM employees "
            r"WHERE employee_id ~ '^[0-9]{6}$'"
        )
    ).scalar()
    next_val = (int(max_val) if max_val is not None else 0) + 1
    # Keep 6-digit padding; if it overflows 999999, return the full number.
    return f"{next_val:06d}" if next_val <= 999999 else str(next_val)


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
    """If current user is not Admin or HR, flash and return redirect; else return None."""
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr'):
        flash('Access denied. This section is for Admin and HR only.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    return None


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
    from sqlalchemy import or_
    stats = {'employees': 0, 'departments': 0, 'leave_requests': 0, 'positions': 0}
    try:
        stats['employees'] = Employee.query.filter(
            or_(Employee.status == 'active', Employee.status == 'on_leave')
        ).count()
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

    We only include employees that are active or on_leave to match the public
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

    base = Employee.query.filter(or_(Employee.status == 'active', Employee.status == 'on_leave'))

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
                .filter(or_(Employee.status == 'active', Employee.status == 'on_leave'))
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
    if current_user.is_authenticated:
        return redirect(url_for(_dashboard_for_user(current_user)))
    return render_template(
        'landing.html',
        stats=_public_landing_stats(),
        birthdays=_public_birthdays_this_week(),
        on_leave=_public_on_leave_this_week(),
        personnel=_public_personnel_breakdown(),
    )

@bp.route('/login', methods=['GET', 'POST'])
def login():
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
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for(_dashboard_for_user(user)))
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
    return render_template('employee_dashboard.html', employee=employee)


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


def _is_regular_employee(emp: Employee) -> bool:
    soa = (emp.status_of_appointment or '').strip().lower()
    if soa:
        return ('permanent' in soa) or ('regular' in soa)
    return not _is_jo_cos_employee(emp)


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


def _pick_work_arrangement_for_date(emp: Employee, d: date):
    """Pick a date-effective arrangement; defaults to 5-day when none matched."""
    if bool(getattr(emp, 'flexible_worktime', False)):
        # Flexible basis: use employee-specific start/end when set, with standard lunch break checkpoints.
        flex_start = getattr(emp, 'flexible_start_time', None)
        flex_end = getattr(emp, 'flexible_end_time', None)
        base = dict(_DTR_DUTY_MODELS['5day'])
        if flex_start:
            base['am_in'] = (flex_start.hour, flex_start.minute)
        if flex_end:
            base['pm_out'] = (flex_end.hour, flex_end.minute)
        return base

    is_regular = _is_regular_employee(emp)
    rows = (DtrWorkArrangementSetting.query
            .filter(DtrWorkArrangementSetting.start_date <= d)
            .filter(DtrWorkArrangementSetting.end_date >= d)
            .order_by(DtrWorkArrangementSetting.created_at.desc(), DtrWorkArrangementSetting.id.desc())
            .all())
    for row in rows:
        applies = (row.applies_to or 'all').strip().lower()
        if applies == 'all':
            return _DTR_DUTY_MODELS.get(row.model_code) or _DTR_DUTY_MODELS['5day']
        if applies == 'regular' and is_regular:
            return _DTR_DUTY_MODELS.get(row.model_code) or _DTR_DUTY_MODELS['5day']
        if applies == 'jo_cos' and not is_regular:
            return _DTR_DUTY_MODELS.get(row.model_code) or _DTR_DUTY_MODELS['5day']
    return _DTR_DUTY_MODELS['5day']


def _late_undertime_minutes_for_record(rec, schedule):
    if not rec or not schedule:
        return 0, 0

    target_am_in = schedule['am_in'][0] * 60 + schedule['am_in'][1]
    target_am_out = schedule['am_out'][0] * 60 + schedule['am_out'][1]
    target_pm_in = schedule['pm_in'][0] * 60 + schedule['pm_in'][1]
    target_pm_out = schedule['pm_out'][0] * 60 + schedule['pm_out'][1]

    am_in = _time_to_minutes(rec.am_in)
    am_out = _time_to_minutes(rec.am_out)
    pm_in = _time_to_minutes(rec.pm_in)
    pm_out = _time_to_minutes(rec.pm_out)

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


def _period_bounds(mode: str, month: int, year: int, quincena: str):
    if mode == 'quincena':
        return _quincena_date_range(year, month, quincena)
    return _month_date_range(year, month)


def _payroll_summary_rows_for_user(role: str, viewer_emp, start_d: date, end_d: date):
    if role == 'payroll_maker' and viewer_emp and viewer_emp.department_id:
        employees = Employee.query.filter_by(status='active', department_id=viewer_emp.department_id).all()
    else:
        employees = Employee.query.filter_by(status='active').all()

    out = []
    for emp in employees:
        dtr_rows = _dtr_rows_for_employee(emp, start_d, end_d)
        by_date = {r.record_date: r for r in dtr_rows}
        leave_dates = _approved_leave_dates_for_employee(emp.id, start_d, end_d)
        curr = start_d
        work_mins = 0
        late_under_mins = 0
        while curr <= end_d:
            sched = _pick_work_arrangement_for_date(emp, curr)
            if sched:
                target_day = (sched['am_out'][0] * 60 + sched['am_out'][1]) - (sched['am_in'][0] * 60 + sched['am_in'][1])
                target_day += (sched['pm_out'][0] * 60 + sched['pm_out'][1]) - (sched['pm_in'][0] * 60 + sched['pm_in'][1])
            else:
                target_day = 8 * 60
            rec = by_date.get(curr)
            if curr in leave_dates:
                work_mins += max(target_day, 0)
            else:
                work_mins += _worked_minutes_from_record(rec)
            if rec:
                late_mins, undertime_mins = _late_undertime_minutes_for_record(rec, sched)
                late_under_mins += (late_mins + undertime_mins)
            curr += timedelta(days=1)
        out.append({
            'employee_id': emp.id,
            'employee_code': emp.employee_id,
            'employee_name': f"{emp.last_name}, {emp.first_name}",
            'work_mins': work_mins,
            'late_under_mins': late_under_mins,
            'leave_days': len(leave_dates),
            'work_hours': f'{work_mins // 60:02d}:{work_mins % 60:02d}',
            'late_under': f'{late_under_mins // 60:02d}:{late_under_mins % 60:02d}',
        })
    out.sort(key=lambda r: r['employee_name'])
    return out


def _payroll_csv_bytes(rows, period_label: str) -> bytes:
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['Payroll Summary', period_label])
    w.writerow(['Employee ID', 'Employee Name', 'Work Hours (HH:MM)', 'Late/Undertime (HH:MM)', 'Approved Leave Days'])
    for r in rows:
        w.writerow([r.get('employee_code', ''), r.get('employee_name', ''), r.get('work_hours', ''), r.get('late_under', ''), r.get('leave_days', 0)])
    return output.getvalue().encode('utf-8')


def _payroll_pdf_bytes(rows, period_label: str) -> io.BytesIO:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    y = height - 50
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, y, 'HRMS Payroll Summary for Accounting Office')
    y -= 18
    c.setFont('Helvetica', 10)
    c.drawString(40, y, f'Period: {period_label}')
    y -= 20
    c.setFont('Helvetica-Bold', 9)
    c.drawString(40, y, 'Employee')
    c.drawString(260, y, 'Work Hrs')
    c.drawString(340, y, 'Late/UT')
    c.drawString(430, y, 'Leave Days')
    y -= 14
    c.setFont('Helvetica', 9)
    for r in rows:
        if y < 50:
            c.showPage()
            y = height - 50
            c.setFont('Helvetica', 9)
        c.drawString(40, y, f"{r.get('employee_name', '')} ({r.get('employee_code', '')})")
        c.drawString(260, y, str(r.get('work_hours', '')))
        c.drawString(340, y, str(r.get('late_under', '')))
        c.drawString(430, y, str(r.get('leave_days', 0)))
        y -= 13
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _month_date_range(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _quincena_date_range(year: int, month: int, quincena: str) -> tuple[date, date]:
    if quincena == '2':
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 16), date(year, month, last_day)
    return date(year, month, 1), date(year, month, 15)


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


@bp.route('/dtr-upload', methods=['GET', 'POST'])
@login_required
def dtr_upload():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role != 'dtr_uploader':
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    employee = current_user.employee if current_user.employee else None
    parsed_rows = []
    today = date.today()
    quincena_options = _build_quincena_upload_options(today)
    default_quincena = f'{today.year:04d}-{today.month:02d}-{"1" if today.day <= 15 else "2"}'
    selected_quincena = default_quincena
    quincena_ok = True
    quincena_missing_dates = []
    if request.method == 'POST':
        selected_quincena = (request.form.get('upload_quincena') or '').strip() or default_quincena
        q_year, q_month, q_half = _parse_quincena_upload_value(selected_quincena)
        if q_year is None:
            flash('Invalid quincena selection.', 'error')
            q_year, q_month, q_half = _parse_quincena_upload_value(default_quincena)
            selected_quincena = default_quincena
        f = request.files.get('dtr_file')
        if f and f.filename:
            try:
                content = f.read()
                parsed_rows = _parse_dtr_dat_file(content, f.filename or '')
                for row in parsed_rows:
                    row['check_in_12h'] = _time_24_to_12(row.get('check_in', ''))
                    row['break_out_12h'] = _time_24_to_12(row.get('break_out', ''))
                    row['break_in_12h'] = _time_24_to_12(row.get('break_in', ''))
                    row['check_out_12h'] = _time_24_to_12(row.get('check_out', ''))
                if not parsed_rows:
                    flash('No valid DTR records found in the file.', 'warning')
                else:
                    row_dates = set()
                    for row in parsed_rows:
                        try:
                            row_dates.add(datetime.strptime(row.get('date', ''), '%Y-%m-%d').date())
                        except ValueError:
                            continue
                    quincena_missing_dates = _missing_dates_for_quincena(row_dates, q_year, q_month, q_half)
                    if quincena_missing_dates:
                        quincena_ok = False
                        flash(
                            'Selected quincena has missing date(s): '
                            + ', '.join(d.strftime('%Y-%m-%d') for d in quincena_missing_dates),
                            'error'
                        )
                    else:
                        quincena_ok = True
                    flash(f'Parsed {len(parsed_rows)} record(s). Review and edit below, then click Save to DTR.', 'success')
            except Exception as e:
                flash(f'Error reading file: {str(e)}', 'error')
    return render_template(
        'dtr_upload.html',
        employee=employee,
        parsed_rows=parsed_rows,
        quincena_options=quincena_options,
        selected_quincena=selected_quincena,
        quincena_ok=quincena_ok,
        quincena_missing_dates=quincena_missing_dates,
        quincena_missing_dates_text=[d.strftime('%Y-%m-%d') for d in quincena_missing_dates],
    )


@bp.route('/dtr-upload/save', methods=['POST'])
@login_required
def dtr_upload_save():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role != 'dtr_uploader':
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    try:
        upload_quincena = (request.form.get('upload_quincena') or '').strip()
        q_year, q_month, q_half = _parse_quincena_upload_value(upload_quincena)
        if q_year is None:
            flash('Missing or invalid quincena selection.', 'error')
            return redirect(url_for('routes.dtr_upload'))

        # Expect form fields: row_N_employee_id, row_N_date, row_N_check_in, row_N_break_out, row_N_break_in, row_N_check_out
        import re
        row_data = []
        for key in request.form:
            m = re.match(r'row_(\d+)_(employee_id|date|check_in|break_out|break_in|check_out)', key)
            if m:
                idx, field = m.group(1), m.group(2)
                while len(row_data) <= int(idx):
                    row_data.append({})
                row_data[int(idx)][field] = request.form.get(key, '').strip()

        row_dates = set()
        for r in row_data:
            raw_date = (r.get('date') or '').strip()
            if not raw_date:
                continue
            for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                try:
                    row_dates.add(datetime.strptime(raw_date, fmt).date())
                    break
                except ValueError:
                    continue
        missing_dates = _missing_dates_for_quincena(row_dates, q_year, q_month, q_half)
        if missing_dates:
            flash(
                'Cannot save: selected quincena has missing date(s): '
                + ', '.join(d.strftime('%Y-%m-%d') for d in missing_dates),
                'error'
            )
            return redirect(url_for('routes.dtr_upload'))

        # Deduplicate by (employee_id, date) and parse times
        seen = set()
        saved = 0
        errors = []
        for r in row_data:
            if not r.get('employee_id') or not r.get('date'):
                continue
            key = (r['employee_id'], r['date'])
            if key in seen:
                continue
            seen.add(key)
            emp = Employee.query.filter_by(employee_id=r['employee_id']).first()
            if not emp:
                errors.append(f"Employee ID {r['employee_id']} not found.")
                continue
            # Accept both YYYY-MM-DD and DD/MM/YYYY from the form
            rec_date = None
            for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                if rec_date is not None:
                    break
                try:
                    rec_date = datetime.strptime(r['date'], fmt).date()
                except ValueError:
                    rec_date = None
            if rec_date is None:
                errors.append(f"Invalid date {r['date']} for employee {r['employee_id']}.")
                continue
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
            am_in = parse_t(r.get('check_in'))
            am_out = parse_t(r.get('break_out'))
            pm_in = parse_t(r.get('break_in'))
            pm_out = parse_t(r.get('check_out'))
            dtr = DailyTimeRecord.query.filter_by(employee_id=emp.id, record_date=rec_date).first()
            if dtr:
                dtr.am_in = am_in
                dtr.am_out = am_out
                dtr.pm_in = pm_in
                dtr.pm_out = pm_out
            else:
                dtr = DailyTimeRecord(
                    employee_id=emp.id,
                    record_date=rec_date,
                    am_in=am_in,
                    am_out=am_out,
                    pm_in=pm_in,
                    pm_out=pm_out,
                )
                db.session.add(dtr)
            saved += 1
        if errors:
            for e in errors:
                flash(e, 'error')
        db.session.commit()
        flash(f'Saved {saved} DTR record(s).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving DTR: {str(e)}', 'error')
    return redirect(url_for('routes.dtr_upload'))


@bp.route('/dtr/generate', methods=['POST'])
@login_required
def dtr_generate():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))

    mode = (request.form.get('mode') or '').strip().lower()  # quincena|month
    month = int((request.form.get('month') or str(date.today().month)).strip())
    year = int((request.form.get('year') or str(date.today().year)).strip())
    quincena = (request.form.get('quincena') or '1').strip()
    arrangement_model = (request.form.get('arrangement_model') or '').strip().lower()
    arrangement_applies_to = (request.form.get('arrangement_applies_to') or 'all').strip().lower()
    arrangement_start_raw = (request.form.get('arrangement_start_date') or '').strip()
    arrangement_end_raw = (request.form.get('arrangement_end_date') or '').strip()

    if mode not in ('quincena', 'month'):
        flash('Invalid generation mode.', 'error')
        return redirect(url_for('routes.dashboard'))

    try:
        if arrangement_model:
            if arrangement_model not in _DTR_DUTY_MODELS:
                flash('Invalid work arrangement model.', 'error')
                return redirect(url_for('routes.dashboard'))
            if arrangement_applies_to not in ('all', 'regular', 'jo_cos'):
                flash('Invalid work arrangement target group.', 'error')
                return redirect(url_for('routes.dashboard'))
            if not arrangement_start_raw or not arrangement_end_raw:
                flash('Work arrangement start and end dates are required.', 'error')
                return redirect(url_for('routes.dashboard'))
            try:
                arrangement_start = datetime.strptime(arrangement_start_raw, '%Y-%m-%d').date()
                arrangement_end = datetime.strptime(arrangement_end_raw, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid work arrangement date format.', 'error')
                return redirect(url_for('routes.dashboard'))
            if arrangement_end < arrangement_start:
                flash('Work arrangement end date must be on/after start date.', 'error')
                return redirect(url_for('routes.dashboard'))
            db.session.add(DtrWorkArrangementSetting(
                model_code=arrangement_model,
                applies_to=arrangement_applies_to,
                start_date=arrangement_start,
                end_date=arrangement_end,
                notes=f'Set from DTR Generate ({mode})',
                created_by_user_id=current_user.id if current_user and getattr(current_user, "id", None) else None,
            ))

        if mode == 'quincena':
            start_d, end_d = _quincena_date_range(year, month, quincena)
            employees = [e for e in Employee.query.filter_by(status='active').all() if _is_jo_cos_employee(e)]
        else:
            start_d, end_d = _month_date_range(year, month)
            employees = [e for e in Employee.query.filter_by(status='active').all() if _is_regular_employee(e)]

        created = 0
        curr = start_d
        while curr <= end_d:
            for emp in employees:
                exists = DailyTimeRecord.query.filter_by(employee_id=emp.id, record_date=curr).first()
                if not exists:
                    db.session.add(DailyTimeRecord(employee_id=emp.id, record_date=curr))
                    created += 1
            curr += timedelta(days=1)
        db.session.commit()
        if arrangement_model:
            flash(
                f'DTR generated for {len(employees)} employee(s). New rows created: {created}. '
                f'Work arrangement saved: {arrangement_model.upper()} ({arrangement_start_raw} to {arrangement_end_raw}, {arrangement_applies_to}).',
                'success'
            )
        else:
            flash(f'DTR generated for {len(employees)} employee(s). New rows created: {created}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error generating DTR: {str(e)}', 'error')
    return redirect(url_for('routes.dashboard'))


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

    if mode == 'quincena':
        start_d, end_d = _quincena_date_range(year, month, quincena)
    else:
        start_d, end_d = _month_date_range(year, month)

    if role in ('admin', 'hr'):
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

    employees = employee_q.order_by(Employee.last_name.asc(), Employee.first_name.asc()).all()
    selected_employee = _resolve_employee_from_scoped_list(employees, selected_employee_id)

    rows = []
    summary = {'lates': 0, 'undertimes': 0, 'absences': 0}
    if selected_employee:
        dtr_rows = _dtr_rows_for_employee(selected_employee, start_d, end_d)
        by_date = {r.record_date: r for r in dtr_rows}
        approved_leave_dates = _approved_leave_dates_for_employee(selected_employee.id, start_d, end_d)
        just_map = {
            j.record_date: j for j in (DtrJustification.query
                                       .filter(DtrJustification.employee_id == selected_employee.id)
                                       .filter(DtrJustification.record_date >= start_d)
                                       .filter(DtrJustification.record_date <= end_d)
                                       .all())
        }
        curr = start_d
        while curr <= end_d:
            rec = by_date.get(curr)
            dow = curr.weekday()
            is_weekend = dow >= 5
            remarks = (rec.remarks if rec and rec.remarks else ('SATURDAY' if dow == 5 else ('SUNDAY' if dow == 6 else '')))
            if curr in approved_leave_dates and not remarks:
                remarks = 'APPROVED LEAVE'
            late = False
            undertime = False
            absent = False
            late_mins = 0
            undertime_mins = 0
            if not is_weekend:
                if rec:
                    schedule = _pick_work_arrangement_for_date(selected_employee, curr)
                    late_mins, undertime_mins = _late_undertime_minutes_for_record(rec, schedule)
                    late = late_mins > 0
                    undertime = undertime_mins > 0
                absent = (not rec or not any([rec.am_in, rec.am_out, rec.pm_in, rec.pm_out])) and not remarks
                if late:
                    summary['lates'] += 1
                if absent:
                    summary['absences'] += 1
                if undertime:
                    summary['undertimes'] += 1

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
            })
            curr += timedelta(days=1)

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
    )


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
        reason = (request.form.get('reason') or '').strip()
        if not rec_date_raw or not reason:
            flash('Date and reason are required.', 'error')
            return redirect(url_for('routes.dtr_justifications'))
        try:
            rec_date = datetime.strptime(rec_date_raw, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date.', 'error')
            return redirect(url_for('routes.dtr_justifications'))
        attachment = request.files.get('attachment')
        attachment_name = None
        if attachment and attachment.filename:
            if not _is_allowed_justification_filename(attachment.filename):
                flash('Unsupported attachment type. Allowed: PDF, PNG, JPG, DOC, DOCX.', 'error')
                return redirect(url_for('routes.dtr_justifications'))
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
        flash('Justification submitted.', 'success')
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


@bp.route('/dtr/recompute', methods=['POST'])
@login_required
def dtr_recompute():
    denied = _require_admin_or_hr()
    if denied:
        return denied
    mode = (request.form.get('mode') or 'month').strip().lower()
    month = int((request.form.get('month') or str(date.today().month)).strip())
    year = int((request.form.get('year') or str(date.today().year)).strip())
    quincena = (request.form.get('quincena') or '1').strip()
    if mode == 'quincena':
        start_d, end_d = _quincena_date_range(year, month, quincena)
    else:
        start_d, end_d = _month_date_range(year, month)

    employees = Employee.query.filter_by(status='active').all()
    updated = 0
    for emp in employees:
        rows = _dtr_rows_for_employee(emp, start_d, end_d)
        by_date = {r.record_date: r for r in rows}
        curr = start_d
        while curr <= end_d:
            rec = by_date.get(curr)
            if rec:
                sched = _pick_work_arrangement_for_date(emp, curr)
                late_mins, undertime_mins = _late_undertime_minutes_for_record(rec, sched)
                total = late_mins + undertime_mins
                rec.undertime_hrs = total // 60
                rec.undertime_mins = total % 60
                updated += 1
            curr += timedelta(days=1)
    db.session.commit()
    flash(f'Recomputed late/undertime totals for {updated} DTR row(s).', 'success')
    return redirect(url_for('routes.dashboard'))


@bp.route('/payroll/summary')
@login_required
def payroll_summary():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr', 'payroll_maker'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))

    employee = current_user.employee if current_user.employee else None
    viewer_emp = _current_employee_for_user()
    mode = (request.args.get('mode') or 'quincena').strip().lower()
    month = int((request.args.get('month') or str(date.today().month)).strip())
    year = int((request.args.get('year') or str(date.today().year)).strip())
    quincena = (request.args.get('quincena') or '1').strip()
    start_d, end_d = _period_bounds(mode, month, year, quincena)
    rows = _payroll_summary_rows_for_user(role, viewer_emp, start_d, end_d)
    return render_template('payroll/summary.html', employee=employee, rows=rows, mode=mode, month=month, year=year, quincena=quincena, start_d=start_d, end_d=end_d)


@bp.route('/payroll/summary/submit', methods=['POST'])
@login_required
def payroll_summary_submit():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('payroll_maker', 'admin', 'hr'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    mode = (request.form.get('mode') or 'quincena').strip().lower()
    month = int((request.form.get('month') or str(date.today().month)).strip())
    year = int((request.form.get('year') or str(date.today().year)).strip())
    quincena = (request.form.get('quincena') or '1').strip()
    summary_json = (request.form.get('summary_json') or '').strip()
    row = PayrollSubmission(
        period_mode=mode,
        year=year,
        month=month,
        quincena=quincena if mode == 'quincena' else None,
        summary_json=summary_json or None,
        submitted_by_user_id=current_user.id if getattr(current_user, 'id', None) else None,
        submitted_to='Accounting Office',
    )
    db.session.add(row)
    db.session.commit()
    flash('Payroll summary submitted to Accounting Office.', 'success')
    return redirect(url_for('routes.payroll_summary', mode=mode, month=month, year=year, quincena=quincena))


@bp.route('/payroll/summary/export')
@login_required
def payroll_summary_export():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr', 'payroll_maker'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    viewer_emp = _current_employee_for_user()
    mode = (request.args.get('mode') or 'quincena').strip().lower()
    month = int((request.args.get('month') or str(date.today().month)).strip())
    year = int((request.args.get('year') or str(date.today().year)).strip())
    quincena = (request.args.get('quincena') or '1').strip()
    fmt = (request.args.get('format') or 'csv').strip().lower()
    start_d, end_d = _period_bounds(mode, month, year, quincena)
    rows = _payroll_summary_rows_for_user(role, viewer_emp, start_d, end_d)
    period_label = f'{start_d.strftime("%Y-%m-%d")} to {end_d.strftime("%Y-%m-%d")}'

    if fmt == 'csv':
        data = _payroll_csv_bytes(rows, period_label)
        return Response(
            data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=payroll_summary_{year}_{month:02d}_{mode}.csv'}
        )

    if fmt == 'pdf':
        buf = _payroll_pdf_bytes(rows, period_label)
        return send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'payroll_summary_{year}_{month:02d}_{mode}.pdf'
        )

    flash('Invalid export format.', 'error')
    return redirect(url_for('routes.payroll_summary', mode=mode, month=month, year=year, quincena=quincena))


@bp.route('/payroll/submissions')
@login_required
def payroll_submissions():
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr', 'payroll_maker'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    year_raw = (request.args.get('year') or '').strip()
    month_raw = (request.args.get('month') or '').strip()
    mode = (request.args.get('mode') or '').strip().lower()
    submitted_by_raw = (request.args.get('submitted_by') or '').strip()

    q = PayrollSubmission.query
    if year_raw:
        try:
            q = q.filter(PayrollSubmission.year == int(year_raw))
        except ValueError:
            flash('Invalid year filter.', 'error')
            return redirect(url_for('routes.payroll_submissions'))
    if month_raw:
        try:
            m = int(month_raw)
            if m < 1 or m > 12:
                raise ValueError()
            q = q.filter(PayrollSubmission.month == m)
        except ValueError:
            flash('Invalid month filter.', 'error')
            return redirect(url_for('routes.payroll_submissions'))
    if mode in ('month', 'quincena'):
        q = q.filter(PayrollSubmission.period_mode == mode)
    if submitted_by_raw:
        try:
            q = q.filter(PayrollSubmission.submitted_by_user_id == int(submitted_by_raw))
        except ValueError:
            flash('Invalid submitted-by filter.', 'error')
            return redirect(url_for('routes.payroll_submissions'))

    rows = q.order_by(PayrollSubmission.created_at.desc(), PayrollSubmission.id.desc()).all()
    submitters = (User.query
                  .join(PayrollSubmission, PayrollSubmission.submitted_by_user_id == User.id)
                  .distinct()
                  .order_by(User.username.asc())
                  .all())
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'payroll/submissions.html',
        employee=employee,
        rows=rows,
        submitters=submitters,
        filters={'year': year_raw, 'month': month_raw, 'mode': mode, 'submitted_by': submitted_by_raw},
    )


@bp.route('/payroll/submissions/<int:id>/export')
@login_required
def payroll_submission_export(id):
    role = (getattr(current_user, 'role', None) or '').strip().lower()
    if role not in ('admin', 'hr', 'payroll_maker'):
        flash('Access denied.', 'error')
        return redirect(url_for(_dashboard_for_user(current_user)))
    row = PayrollSubmission.query.get_or_404(id)
    fmt = (request.args.get('format') or 'csv').strip().lower()
    try:
        rows = json.loads(row.summary_json or '[]')
        if not isinstance(rows, list):
            rows = []
    except json.JSONDecodeError:
        rows = []
    mode = row.period_mode or 'month'
    month = int(row.month or 1)
    year = int(row.year or date.today().year)
    quincena = row.quincena or '1'
    start_d, end_d = _period_bounds(mode, month, year, quincena)
    period_label = f'{start_d.strftime("%Y-%m-%d")} to {end_d.strftime("%Y-%m-%d")}'
    base = f'payroll_submission_{id}_{year}_{month:02d}_{mode}'

    if fmt == 'csv':
        data = _payroll_csv_bytes(rows, period_label)
        return Response(
            data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={base}.csv'}
        )
    if fmt == 'pdf':
        buf = _payroll_pdf_bytes(rows, period_label)
        return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f'{base}.pdf')
    flash('Invalid export format.', 'error')
    return redirect(url_for('routes.payroll_submissions'))


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
            f"{result['skipped_terminated']} terminated (skipped)."
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


@bp.route('/leave-credits/<int:id>')
@login_required
def leave_credits_card(id):
    """Leave card for a single employee: show leave ledger transactions."""
    denied = _require_admin_or_hr()
    if denied:
        return denied
    emp = Employee.query.get_or_404(id)
    # Display ledger entries newest-first (balances are stored per row).
    ledger_entries = (LeaveLedger.query
                      .filter_by(employee_id=emp.id)
                      .order_by(LeaveLedger.transaction_date.desc(), LeaveLedger.id.desc())
                      .all())
    employee = current_user.employee if current_user.employee else None
    return render_template('leave_credits/card.html',
                           emp=emp,
                           ledger_entries=ledger_entries,
                           employee=employee)


@bp.route('/leave-credits/<int:id>/add', methods=['POST'])
@login_required
def leave_ledger_add(id):
    """Add or edit a leave ledger entry for an employee and recompute running balances."""
    denied = _require_admin_or_hr()
    if denied:
        return denied

    emp = Employee.query.get_or_404(id)

    try:
        entry_id = (request.form.get('entry_id') or '').strip()
        transaction_date_str = (request.form.get('transaction_date') or '').strip()
        particulars = (request.form.get('particulars') or '').strip()
        remarks = (request.form.get('remarks') or '').strip() or None

        if not transaction_date_str or not particulars:
            flash('Date and Particulars are required.', 'error')
            return redirect(url_for('routes.leave_credits_card', id=id))

        transaction_date = datetime.strptime(transaction_date_str, '%Y-%m-%d').date()

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
        ]
        deltas = {name: get_decimal(name) for name in delta_fields}

        # Load all entries for this employee ordered by date then id
        entries = (LeaveLedger.query
                   .filter_by(employee_id=emp.id)
                   .order_by(LeaveLedger.transaction_date, LeaveLedger.id)
                   .all())

        if entry_id:
            # Edit existing entry: replace deltas and meta
            target = next((e for e in entries if e.id == int(entry_id)), None)
            if not target:
                flash('Ledger entry not found.', 'error')
                return redirect(url_for('routes.leave_credits_card', id=id))
            target.transaction_date = transaction_date
            target.particulars = particulars
            target.remarks = remarks
            for k, v in deltas.items():
                setattr(target, k, v)
        else:
            # Add new entry with these deltas
            target = LeaveLedger(
                employee_id=emp.id,
                transaction_date=transaction_date,
                particulars=particulars,
                remarks=remarks,
                created_by=current_user.id,
            )
            for k, v in deltas.items():
                setattr(target, k, v)
            db.session.add(target)
            db.session.flush()

        recompute_leave_ledger_balances(emp.id)

        db.session.commit()
        flash('Leave ledger entry saved successfully.', 'success')

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

    # Step increment due: appointment_date + 3 years, notify when due in next 15 days
    today = date.today()
    window_end = today + timedelta(days=15)
    step_increment_due_count = 0
    for emp in Employee.query.filter(Employee.appointment_date.isnot(None)).all():
        due = _add_years(emp.appointment_date, 3)
        if due and today <= due <= window_end:
            step_increment_due_count += 1
    
    # Get recent leave requests
    recent_leaves = LeaveRequest.query.order_by(LeaveRequest.created_at.desc()).limit(10).all()

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
                         employee=employee)

# Employee Management Routes
@bp.route('/employees')
@login_required
def employees_list():
    employees = Employee.query.order_by(Employee.created_at.desc()).all()
    departments = Department.query.all()
    employee = current_user.employee if current_user.employee else None
    return render_template('employees/list.html', employees=employees, departments=departments, employee=employee)


@bp.route('/about/users-manual')
@login_required
def users_manual():
    employee = current_user.employee if current_user.employee else None
    return render_template('about/users_manual.html', employee=employee)


@bp.route('/about/hrmdo-manual')
@login_required
def hrmdo_manual():
    employee = current_user.employee if current_user.employee else None
    return render_template('about/hrmdo_manual.html', employee=employee)

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
            position = request.form.get('position', '').strip()
            department_id = request.form.get('department_id')
            status_of_appointment = request.form.get('status_of_appointment', '').strip()
            nature_of_appointment = request.form.get('nature_of_appointment', '').strip()
            appointment_date = request.form.get('appointment_date')
            phone = request.form.get('phone', '').strip()
            mobile_no = request.form.get('mobile_no', '').strip()
            lgu_class_level = request.form.get('lgu_class_level', '').strip()
            salary_tranche = request.form.get('salary_tranche', '').strip()
            salary_grade_val = request.form.get('salary_grade', '').strip()
            salary_step_val = request.form.get('salary_step', '').strip()
            flexible_worktime = request.form.get('flexible_worktime') == '1'
            flexible_start_raw = request.form.get('flexible_start_time', '').strip()
            flexible_end_raw = request.form.get('flexible_end_time', '').strip()
            # Address fields
            residential_house_no = request.form.get('residential_house_no', '').strip()
            residential_street = request.form.get('residential_street', '').strip()
            residential_subdivision = request.form.get('residential_subdivision', '').strip()
            residential_barangay = request.form.get('residential_barangay', '').strip()
            residential_city = request.form.get('residential_city', '').strip()
            residential_province = request.form.get('residential_province', '').strip()
            residential_zip_code = request.form.get('residential_zip_code', '').strip()
            address = request.form.get('address', '').strip()  # Keep for backward compatibility
            status = request.form.get('status', 'active')
            
            # Agency: National if department name is RHU, else Local
            agency = None
            if department_id:
                dept = Department.query.get(int(department_id))
                agency = 'National' if dept and dept.name == 'RHU' else 'Local'

            flexible_start_time = None
            flexible_end_time = None
            if flexible_worktime:
                try:
                    if flexible_start_raw:
                        flexible_start_time = datetime.strptime(flexible_start_raw, '%H:%M').time()
                    if flexible_end_raw:
                        flexible_end_time = datetime.strptime(flexible_end_raw, '%H:%M').time()
                except ValueError:
                    flash('Invalid flexible worktime start/end format.', 'error')
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
                        next_employee_id=_next_employee_id_6digit(),
                        employee=employee,
                    )

            # Automatic employee_id generation: max 6-digit numeric + 1.
            # Also includes a small retry loop to avoid collisions in concurrent adds.
            new_employee = None
            for _ in range(5):
                employee_id = _next_employee_id_6digit()
                try:
                    new_employee = Employee(
                        employee_id=employee_id,
                        first_name=first_name,
                        last_name=last_name,
                        middle_name=middle_name if middle_name else None,
                        position=position if position else None,
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
                        flexible_worktime=flexible_worktime,
                        flexible_start_time=flexible_start_time,
                        flexible_end_time=flexible_end_time,
                        residential_house_no=residential_house_no if residential_house_no else None,
                        residential_street=residential_street if residential_street else None,
                        residential_subdivision=residential_subdivision if residential_subdivision else None,
                        residential_barangay=residential_barangay if residential_barangay else None,
                        residential_city=residential_city if residential_city else None,
                        residential_province=residential_province if residential_province else None,
                        residential_zip_code=residential_zip_code if residential_zip_code else None,
                        address=address if address else None,  # Keep for backward compatibility
                        status=status
                    )

                    db.session.add(new_employee)
                    db.session.flush()  # Assign new_employee.id and verify unique constraints
                    break
                except IntegrityError:
                    db.session.rollback()
                    new_employee = None
                    continue

            if not new_employee:
                raise RuntimeError("Could not generate a unique Employee ID. Please retry.")
            
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
                next_employee_id=_next_employee_id_6digit(),
                employee=employee,
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
        next_employee_id=_next_employee_id_6digit(),
        employee=employee,
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
            position = request.form.get('position', '').strip()
            department_id = request.form.get('department_id')
            status_of_appointment = request.form.get('status_of_appointment', '').strip()
            nature_of_appointment = request.form.get('nature_of_appointment', '').strip()
            appointment_date = request.form.get('appointment_date')
            phone = request.form.get('phone', '').strip()
            mobile_no = request.form.get('mobile_no', '').strip()
            lgu_class_level = request.form.get('lgu_class_level', '').strip()
            salary_tranche = request.form.get('salary_tranche', '').strip()
            salary_grade_val = request.form.get('salary_grade', '').strip()
            salary_step_val = request.form.get('salary_step', '').strip()
            flexible_worktime = request.form.get('flexible_worktime') == '1'
            flexible_start_raw = request.form.get('flexible_start_time', '').strip()
            flexible_end_raw = request.form.get('flexible_end_time', '').strip()
            # Address fields
            residential_house_no = request.form.get('residential_house_no', '').strip()
            residential_street = request.form.get('residential_street', '').strip()
            residential_subdivision = request.form.get('residential_subdivision', '').strip()
            residential_barangay = request.form.get('residential_barangay', '').strip()
            residential_city = request.form.get('residential_city', '').strip()
            residential_province = request.form.get('residential_province', '').strip()
            residential_zip_code = request.form.get('residential_zip_code', '').strip()
            address = request.form.get('address', '').strip()  # Keep for backward compatibility
            status = request.form.get('status', 'active')
            
            # Check if employee_id already exists (excluding current employee)
            existing = Employee.query.filter_by(employee_id=employee_id).first()
            if existing and existing.id != id:
                flash('Employee ID already exists.', 'error')
                departments = Department.query.all()
                positions = Position.query.order_by(Position.title).all()
                salary_grades = SalaryGrade.query.all()
                employee = current_user.employee if current_user.employee else None
                salary_grades_json = _serialize_salary_grades(salary_grades)
                return render_template('employees/form.html', emp=emp, departments=departments, positions=positions, salary_grades=salary_grades, salary_grades_json=salary_grades_json, employee=employee)

            # Capture old appointment-related values for history (before update)
            old_appointment_date = emp.appointment_date
            old_status_of_appointment = emp.status_of_appointment
            old_nature_of_appointment = emp.nature_of_appointment
            old_agency = emp.agency
            old_lgu_class_level = emp.lgu_class_level
            old_salary_tranche = emp.salary_tranche
            old_salary_grade = emp.salary_grade
            old_salary_step = emp.salary_step

            # Agency: National if department name is RHU, else Local
            agency = None
            dept = None
            if department_id:
                dept = Department.query.get(int(department_id))
                agency = 'National' if dept and dept.name == 'RHU' else 'Local'

            # New appointment/salary values (for change detection)
            new_appointment_date = datetime.strptime(appointment_date, '%Y-%m-%d').date() if appointment_date else None
            new_status = status_of_appointment if status_of_appointment else None
            new_nature = nature_of_appointment if nature_of_appointment else None
            new_lgu = lgu_class_level if lgu_class_level else None
            new_tranche = salary_tranche if salary_tranche else None
            new_sal_grade = int(salary_grade_val) if salary_grade_val else None
            new_sal_step = int(salary_step_val) if salary_step_val else None
            flexible_start_time = None
            flexible_end_time = None
            if flexible_worktime:
                try:
                    if flexible_start_raw:
                        flexible_start_time = datetime.strptime(flexible_start_raw, '%H:%M').time()
                    if flexible_end_raw:
                        flexible_end_time = datetime.strptime(flexible_end_raw, '%H:%M').time()
                except ValueError:
                    flash('Invalid flexible worktime start/end format.', 'error')
                    departments = Department.query.all()
                    positions = Position.query.order_by(Position.title).all()
                    salary_grades = SalaryGrade.query.all()
                    employee = current_user.employee if current_user.employee else None
                    salary_grades_json = _serialize_salary_grades(salary_grades)
                    return render_template('employees/form.html', emp=emp, departments=departments, positions=positions, salary_grades=salary_grades, salary_grades_json=salary_grades_json, employee=employee)

            # Update employee
            emp.employee_id = employee_id
            emp.first_name = first_name
            emp.last_name = last_name
            emp.middle_name = middle_name if middle_name else None
            emp.position = position if position else None
            emp.department_id = int(department_id) if department_id else None
            emp.status_of_appointment = status_of_appointment if status_of_appointment else None
            emp.nature_of_appointment = nature_of_appointment if nature_of_appointment else None
            emp.appointment_date = datetime.strptime(appointment_date, '%Y-%m-%d').date() if appointment_date else None
            emp.phone = phone if phone else None
            emp.mobile_no = mobile_no if mobile_no else None
            emp.agency = agency
            emp.lgu_class_level = lgu_class_level if lgu_class_level else None
            emp.salary_tranche = salary_tranche if salary_tranche else None
            emp.salary_grade = int(salary_grade_val) if salary_grade_val else None
            emp.salary_step = int(salary_step_val) if salary_step_val else None
            emp.flexible_worktime = flexible_worktime
            emp.flexible_start_time = flexible_start_time
            emp.flexible_end_time = flexible_end_time
            emp.residential_house_no = residential_house_no if residential_house_no else None
            emp.residential_street = residential_street if residential_street else None
            emp.residential_subdivision = residential_subdivision if residential_subdivision else None
            emp.residential_barangay = residential_barangay if residential_barangay else None
            emp.residential_city = residential_city if residential_city else None
            emp.residential_province = residential_province if residential_province else None
            emp.residential_zip_code = residential_zip_code if residential_zip_code else None
            emp.address = address if address else None  # Keep for backward compatibility
            emp.status = status
            emp.updated_at = datetime.utcnow()
            
            # Get user email if employee has a user account
            user_email = None
            if emp.user_id:
                user = User.query.get(emp.user_id)
                if user:
                    user_email = user.email
            
            # Sync address, mobile, phone, and email to employee_pds table
            pds = EmployeePDS.query.filter_by(employee_id=id).first()
            if pds:
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

            # If appointment/salary-related fields changed, append to history
            appointment_changed = (
                old_appointment_date != new_appointment_date
                or old_status_of_appointment != new_status
                or old_nature_of_appointment != new_nature
                or old_agency != agency
                or old_lgu_class_level != new_lgu
                or old_salary_tranche != new_tranche
                or old_salary_grade != new_sal_grade
                or old_salary_step != new_sal_step
            )
            if appointment_changed:
                from sqlalchemy import func as sql_func
                emp_dept_name = dept.name if dept else (emp.department.name if emp.department else None)
                sal_amount = None
                if emp.salary_grade and emp.salary_step and (agency or emp.agency) is not None:
                    sg_row = SalaryGrade.query.filter(
                        SalaryGrade.sg == emp.salary_grade,
                        sql_func.coalesce(SalaryGrade.sg_agency, '') == (agency or emp.agency or ''),
                        sql_func.coalesce(SalaryGrade.sg_lgu_class, '') == (emp.lgu_class_level or ''),
                        sql_func.coalesce(SalaryGrade.sg_tranche, '') == (emp.salary_tranche or '')
                    ).first()
                    if sg_row and 1 <= emp.salary_step <= 8:
                        sal_amount = getattr(sg_row, f'sg_step_{emp.salary_step}', None)
                history_entry = EmployeeAppointmentHistory(
                    emp_id=emp.id,
                    emp_dept=emp_dept_name,
                    emp_position=emp.position,
                    appoint_date=emp.appointment_date,
                    appoint_status=emp.status_of_appointment,
                    appoint_nature=emp.nature_of_appointment,
                    agency=emp.agency,
                    lgu_class=emp.lgu_class_level,
                    sal_grade=emp.salary_grade,
                    sal_step=emp.salary_step,
                    sal_amount=sal_amount,
                    location=request.remote_addr,
                    user_id=current_user.id if current_user else None
                )
                db.session.add(history_entry)

            ensure_appointment_spl_wl_grant(
                emp.id,
                created_by_user_id=getattr(current_user, 'id', None),
            )
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
            return render_template('employees/form.html', emp=emp, departments=departments, positions=positions, salary_grades=salary_grades, salary_grades_json=salary_grades_json, employee=employee)
    
    departments = Department.query.all()
    positions = Position.query.order_by(Position.title).all()
    salary_grades = SalaryGrade.query.all()
    employee = current_user.employee if current_user.employee else None
    salary_grades_json = _serialize_salary_grades(salary_grades)
    return render_template('employees/form.html', emp=emp, departments=departments, positions=positions, salary_grades=salary_grades, salary_grades_json=salary_grades_json, employee=employee)

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
        
        # Delete dependent rows first (PDS has NOT NULL employee_id FK, so it must be deleted)
        EmployeePDS.query.filter_by(employee_id=emp.id).delete(synchronize_session=False)
        ledger_rows = LeaveLedger.query.filter_by(employee_id=emp.id).all()
        for ledger_entry in ledger_rows:
            record_leave_ledger_deletion(
                ledger_entry,
                deleted_by_user_id=current_user.id,
                deleted_by_username=getattr(current_user, 'username', None),
                source='employee_delete',
            )
        LeaveLedger.query.filter_by(employee_id=emp.id).delete(synchronize_session=False)
        LeaveRequest.query.filter_by(employee_id=emp.id).delete(synchronize_session=False)
        DailyTimeRecord.query.filter_by(employee_id=emp.id).delete(synchronize_session=False)
        Attendance.query.filter_by(employee_id=emp.id).delete(synchronize_session=False)
        EmployeeAppointmentHistory.query.filter_by(emp_id=emp.id).delete(synchronize_session=False)

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
                pds.name_extension = request.form.get('name_extension', '').strip() or None
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
    users = User.query.order_by(User.created_at.desc()).all()
    employees = Employee.query.filter_by(user_id=None).order_by(Employee.employee_id).all()
    employee = current_user.employee if current_user.employee else None
    return render_template('users/list.html', users=users, employees=employees, employee=employee)

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
        # Unlink employee if exists
        if user.employee:
            user.employee.user_id = None
        
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
    today = date.today()
    window_end = today + timedelta(days=15)
    step_increment_due_count = 0
    step_increment_due_list = []
    employees_with_amount = []
    for emp in employees:
        amount = _current_salary_amount(emp)
        # appointment_history is lazy='dynamic', so do not joinedload it; query it per employee
        history = list(emp.appointment_history.order_by(EmployeeAppointmentHistory.timestamp.desc()).all())
        employees_with_amount.append((emp, amount, history))
        # Step increment due: appointment_date + 3 years, notify 15 days before (window: [today, today+15])
        if emp.appointment_date:
            due_date = _add_years(emp.appointment_date, 3)
            if due_date is not None and today <= due_date <= window_end:
                step_increment_due_count += 1
                step_increment_due_list.append((emp, due_date))
    employee = current_user.employee if current_user.employee else None
    return render_template(
        'status_of_appointment/list.html',
        employees_with_amount=employees_with_amount,
        employee=employee,
        step_increment_due_count=step_increment_due_count,
        step_increment_due_list=step_increment_due_list,
    )


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
