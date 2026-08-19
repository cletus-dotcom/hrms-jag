"""
One-time migration: employee employment status values + separation columns.

- Adds separation_date / separation_type columns if missing
- Converts legacy status values:
    on_leave   -> inactive
    terminated -> separated

Safe to run multiple times (idempotent).
"""

from __future__ import annotations

import sys

from sqlalchemy import bindparam, inspect, text

from app import create_app, db
from app.config import EMPLOYEE_SEPARATION_TYPES


def run_employee_status_migration() -> dict:
    """Run migration inside an existing Flask app context."""
    result = {
        'columns_added': [],
        'inactive_from_on_leave': 0,
        'separated_from_terminated': 0,
    }

    inspector = inspect(db.engine)
    if 'employees' not in inspector.get_table_names():
        return result

    columns = [c['name'] for c in inspector.get_columns('employees')]
    for name, typ in (
        ('separation_date', 'DATE'),
        ('separation_type', 'VARCHAR(60)'),
    ):
        if name not in columns:
            db.session.execute(text(f'ALTER TABLE employees ADD COLUMN {name} {typ}'))
            result['columns_added'].append(name)

    # Best-effort backfill: if a legacy free-text column exists and matches an allowed option,
    # copy it into the new separation_type column.
    columns_now = {c['name'] for c in inspector.get_columns('employees')}
    if 'separation_reason' in columns_now and 'separation_type' in columns_now and EMPLOYEE_SEPARATION_TYPES:
        stmt = text(
            """
            UPDATE employees
            SET separation_type = separation_reason
            WHERE separation_type IS NULL
              AND separation_reason IS NOT NULL
              AND separation_reason IN :allowed
            """
        ).bindparams(bindparam('allowed', expanding=True))
        db.session.execute(stmt, {'allowed': list(EMPLOYEE_SEPARATION_TYPES)})

    inactive = db.session.execute(
        text("""
            UPDATE employees
            SET status = 'inactive'
            WHERE LOWER(TRIM(status)) = 'on_leave'
        """)
    )
    separated = db.session.execute(
        text("""
            UPDATE employees
            SET status = 'separated'
            WHERE LOWER(TRIM(status)) = 'terminated'
        """)
    )
    db.session.commit()

    result['inactive_from_on_leave'] = inactive.rowcount or 0
    result['separated_from_terminated'] = separated.rowcount or 0
    return result


def migrate_employee_status_values() -> dict:
    """CLI entry: create app and run migration."""
    app = create_app()
    with app.app_context():
        try:
            result = run_employee_status_migration()
            if result['columns_added']:
                print(f"[OK] Added columns: {', '.join(result['columns_added'])}")
            else:
                print('[OK] Separation columns already present')
            print(
                f"[OK] Status migration: "
                f"{result['inactive_from_on_leave']} on_leave -> inactive, "
                f"{result['separated_from_terminated']} terminated -> separated"
            )
            return result
        except Exception as exc:
            db.session.rollback()
            print(f'[ERROR] Migration failed: {exc}')
            raise


if __name__ == '__main__':
    try:
        migrate_employee_status_values()
        sys.exit(0)
    except Exception:
        sys.exit(1)
