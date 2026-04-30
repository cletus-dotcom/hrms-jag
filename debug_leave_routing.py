from app import create_app
from app.models import LeaveRequest, Employee, Department, User


def _employee_snapshot(emp_code: str) -> None:
    emp = Employee.query.filter_by(employee_id=emp_code).first()
    print("== employee_id:", emp_code)
    if not emp:
        print("  NOT FOUND")
        return
    dept_name = emp.department.name if emp.department else None
    user_role = emp.user.role if getattr(emp, "user", None) else None
    print("  emp.id:", emp.id, "dept:", dept_name, "user_id:", emp.user_id, "user_role:", user_role)

    lr = (
        LeaveRequest.query.filter_by(employee_id=emp.id)
        .order_by(LeaveRequest.created_at.desc())
        .first()
    )
    if not lr:
        print("  leave: NONE")
        return

    reason = lr.reason or ""
    print("  leave.id:", lr.id, "status:", lr.status, "created_at:", lr.created_at)
    print("  has_special_meta:", "__META__SPECIAL_APPROVAL=1" in reason)


def _dept_head_snapshot() -> None:
    from sqlalchemy import func as sql_func

    mo = (
        Department.query.filter(
            sql_func.lower(Department.name).in_(
                ["mo", "mayor's office", "mayors office", "municipal mayor"]
            )
        ).first()
        or Department.query.filter(sql_func.lower(Department.name).like("%mayor%")).first()
    )
    hrmdo = (
        Department.query.filter(sql_func.lower(Department.name).like("%hrmdo%")).first()
        or Department.query.filter(sql_func.lower(Department.name).like("%hrmo%")).first()
        or Department.query.filter(sql_func.lower(Department.name).like("%mado%")).first()
    )
    mado = (
        Department.query.filter(sql_func.lower(Department.name).like("%mado%")).first()
        or Department.query.filter(sql_func.lower(Department.name).like("%administrator%")).first()
    )

    print("== resolved departments for approvals")
    print("  MO dept:", (mo.name if mo else None), "manager_id:", (mo.manager_id if mo else None))
    print("  HRMDO/HRMO dept:", (hrmdo.name if hrmdo else None), "manager_id:", (hrmdo.manager_id if hrmdo else None))
    print("  MADO dept:", (mado.name if mado else None), "manager_id:", (mado.manager_id if mado else None))


def _user_snapshot(user_key: str) -> None:
    """
    user_key can be:
    - users.id (int-like string)
    - users.employee_id (employee number string)
    """
    u = None
    try:
        u = User.query.get(int(user_key))
    except Exception:
        u = None
    if not u:
        u = User.query.filter_by(employee_id=str(user_key)).first()

    print("== user key:", user_key)
    if not u:
        print("  NOT FOUND in users.id or users.employee_id")
        return
    emp = u.employee
    print("  user.id:", u.id, "username:", u.username, "role:", u.role, "users.employee_id:", u.employee_id)
    if not emp:
        print("  linked employee: NONE")
        return
    print(
        "  emp.id:",
        emp.id,
        "emp.employee_id:",
        emp.employee_id,
        "dept:",
        (emp.department.name if emp.department else None),
        "department_id:",
        emp.department_id,
    )


def _pending_visible_for_user(user_key: str) -> None:
    """
    Approximate which pending leave IDs would be visible in /leave-approvals
    for this user under the current routing rules.
    """
    u = None
    try:
        u = User.query.get(int(user_key))
    except Exception:
        u = None
    if not u:
        u = User.query.filter_by(employee_id=str(user_key)).first()
    if not u:
        print("== approvals visibility for:", user_key, "(NOT FOUND)")
        return

    role = (u.role or "").strip().lower()
    emp = u.employee
    emp_id = emp.id if emp else None

    from sqlalchemy import func as sql_func

    # special condition (backward compatible)
    special_cond = (
        (LeaveRequest.reason.ilike("%__META__SPECIAL_APPROVAL=1%"))
        | (sql_func.lower(User.role).in_(["manager", "admin"]))
        | (sql_func.lower(Department.name).in_(["mo", "mayor's office", "mayors office", "municipal mayor"]))
        | (sql_func.lower(Department.name).like("%mayor%"))
    )

    # dept-managed ids (manager role)
    dept_ids = []
    if emp_id and role == "manager":
        dept_ids = [d.id for d in Department.query.filter_by(manager_id=emp_id).all()]

    pending_ids = set()

    if role == "manager" and dept_ids:
        pending_ids |= {
            r[0]
            for r in (
                LeaveRequest.query.with_entities(LeaveRequest.id)
                .join(Employee, Employee.id == LeaveRequest.employee_id)
                .filter(LeaveRequest.status == "pending", Employee.department_id.in_(dept_ids))
                .all()
            )
        }

    # special approver if dept head of MO/HRMDO/HRMO/MADO (manager or admin)
    is_dept_head = False
    if emp_id and role in ("manager", "admin"):
        mo = (
            Department.query.filter(
                sql_func.lower(Department.name).in_(["mo", "mayor's office", "mayors office", "municipal mayor"])
            ).first()
            or Department.query.filter(sql_func.lower(Department.name).like("%mayor%")).first()
        )
        hrmdo = Department.query.filter(sql_func.lower(Department.name).like("%hrmdo%")).first() or Department.query.filter(
            sql_func.lower(Department.name).like("%hrmo%")
        ).first()
        mado = Department.query.filter(sql_func.lower(Department.name).like("%mado%")).first()
        is_dept_head = bool(
            (mo and mo.manager_id == emp_id) or (hrmdo and hrmdo.manager_id == emp_id) or (mado and mado.manager_id == emp_id)
        )

    if is_dept_head:
        pending_ids |= {
            r[0]
            for r in (
                LeaveRequest.query.with_entities(LeaveRequest.id)
                .join(Employee, Employee.id == LeaveRequest.employee_id)
                .outerjoin(Department, Department.id == Employee.department_id)
                .outerjoin(User, User.id == Employee.user_id)
                .filter(LeaveRequest.status == "pending")
                .filter(special_cond)
                .all()
            )
        }

    # admin fallback: see manager-filed pending approvals
    if role == "admin" and not is_dept_head:
        pending_ids |= {
            r[0]
            for r in (
                LeaveRequest.query.with_entities(LeaveRequest.id)
                .join(Employee, Employee.id == LeaveRequest.employee_id)
                .outerjoin(User, User.id == Employee.user_id)
                .filter(LeaveRequest.status == "pending")
                .filter(sql_func.lower(User.role) == "manager")
                .all()
            )
        }

    # manager cannot act on own leave
    if role == "manager" and emp_id:
        pending_ids = {lid for lid in pending_ids if (LeaveRequest.query.get(lid).employee_id != emp_id)}

    print("== approvals visibility for:", user_key, "user.id=", u.id, "role=", role, "emp.id=", emp_id)
    print("  pending ids:", sorted(pending_ids))


def main() -> None:
    app = create_app()
    with app.app_context():
        _employee_snapshot("22651")
        _employee_snapshot("40555")
        _user_snapshot("35008")
        _user_snapshot("40369")
        _pending_visible_for_user("35008")
        _pending_visible_for_user("40369")
        _dept_head_snapshot()


if __name__ == "__main__":
    main()

