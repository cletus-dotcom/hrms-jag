"""
Optional migration script:
- Renames employees.hire_date -> employees.appointment_date in Postgres (if desired)

NOTE:
The application code currently maps Employee.appointment_date to the underlying
DB column name "hire_date" for backward compatibility. This migration is only
needed if you want the physical column name to be "appointment_date".
"""

import sys
from sqlalchemy import text
from app import create_app, db


def migrate_employees_appointment_date():
    app = create_app()

    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            columns = [col["name"] for col in inspector.get_columns("employees")]

            # If the new column name already exists, nothing to do.
            if "appointment_date" in columns:
                print("[OK] employees.appointment_date already exists")
                return True

            # If the old column doesn't exist, nothing to rename.
            if "hire_date" not in columns:
                print("[OK] employees.hire_date not found; nothing to rename")
                return True

            print("Renaming employees.hire_date -> employees.appointment_date ...")
            db.session.execute(text("ALTER TABLE employees RENAME COLUMN hire_date TO appointment_date"))
            db.session.commit()
            print("[OK] Column renamed successfully")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Migration failed: {e}")
            return False


if __name__ == "__main__":
    success = migrate_employees_appointment_date()
    sys.exit(0 if success else 1)

