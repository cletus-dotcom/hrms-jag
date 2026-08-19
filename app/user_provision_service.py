"""Bulk user provisioning for active employees."""

from __future__ import annotations

import re

from app import db
from app.models import Employee, User
from app.portal_config import is_reserved_portal_username

DEFAULT_USER_ROLE = 'employee'
EMAIL_DOMAIN = 'hrms.local'


def _default_user_email(employee_id: str, employee_pk: int | None = None) -> str:
    safe = re.sub(r'[^\w.\-]', '', (employee_id or '').strip()) or f'emp{employee_pk or 0}'
    base = f'{safe.lower()}@{EMAIL_DOMAIN}'
    if not User.query.filter_by(email=base).first():
        return base
    return f'{safe.lower()}.{employee_pk}@{EMAIL_DOMAIN}'


def count_active_employees_without_user() -> int:
    linked_ids = {
        row[0]
        for row in db.session.query(Employee.id)
        .filter(Employee.user_id.isnot(None))
        .all()
    }
    existing_emp_ids = {
        row[0]
        for row in db.session.query(User.employee_id).all()
    }
    count = 0
    for emp in Employee.query.filter(Employee.status == 'active').all():
        if emp.id in linked_ids:
            continue
        if emp.employee_id in existing_emp_ids:
            continue
        if User.query.filter_by(username=emp.employee_id).first():
            continue
        if is_reserved_portal_username(emp.employee_id):
            continue
        count += 1
    return count


def provision_users_for_active_employees() -> dict:
    """
    Create user accounts for active employees without one.

    Username and initial password = employees.employee_id (badge number).
    Skips employees who already have a linked user or matching users.employee_id.
    """
    created = 0
    linked = 0
    skipped = 0
    errors: list[str] = []

    active_employees = (
        Employee.query.filter(Employee.status == 'active')
        .order_by(Employee.employee_id)
        .all()
    )

    for emp in active_employees:
        badge = (emp.employee_id or '').strip()
        if not badge:
            skipped += 1
            errors.append(f'Employee pk {emp.id}: missing employee_id')
            continue

        if is_reserved_portal_username(badge):
            skipped += 1
            continue

        existing = User.query.filter_by(employee_id=badge).first()
        if existing:
            if emp.user_id != existing.id:
                emp.user_id = existing.id
                linked += 1
            else:
                skipped += 1
            continue

        if emp.user_id:
            skipped += 1
            continue

        if User.query.filter_by(username=badge).first():
            skipped += 1
            errors.append(f'{badge}: username already taken by another account')
            continue

        email = _default_user_email(badge, emp.id)
        if User.query.filter_by(email=email).first():
            skipped += 1
            errors.append(f'{badge}: could not allocate unique email')
            continue

        try:
            with db.session.begin_nested():
                user = User(
                    username=badge,
                    email=email,
                    employee_id=badge,
                    role=DEFAULT_USER_ROLE,
                    is_active=True,
                )
                user.set_password(badge)
                db.session.add(user)
                db.session.flush()
                emp.user_id = user.id
            created += 1
        except Exception as exc:
            errors.append(f'{badge}: {exc}')
            if len(errors) >= 20:
                break

    if errors and created == 0 and linked == 0:
        return {
            'ok': False,
            'created': 0,
            'linked': 0,
            'skipped': skipped,
            'errors': errors,
        }

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return {
            'ok': False,
            'created': 0,
            'linked': 0,
            'skipped': skipped,
            'errors': [str(exc)] + errors[:10],
        }

    return {
        'ok': True,
        'created': created,
        'linked': linked,
        'skipped': skipped,
        'errors': errors[:10],
    }
