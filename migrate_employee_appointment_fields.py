"""
Migration script to add status_of_appointment and nature_of_appointment to employees table.
Run: python migrate_employee_appointment_fields.py
"""
import sys
from sqlalchemy import text
from app import create_app, db


def migrate():
    app = create_app()
    with app.app_context():
        try:
            print("Adding status_of_appointment and nature_of_appointment to employees...")
            inspector = db.inspect(db.engine)
            columns = [col["name"] for col in inspector.get_columns("employees")]

            if "status_of_appointment" not in columns:
                db.session.execute(text("ALTER TABLE employees ADD COLUMN status_of_appointment VARCHAR(50)"))
                print("[OK] status_of_appointment added")
            else:
                print("[OK] status_of_appointment already exists")

            if "nature_of_appointment" not in columns:
                db.session.execute(text("ALTER TABLE employees ADD COLUMN nature_of_appointment VARCHAR(50)"))
                print("[OK] nature_of_appointment added")
            else:
                print("[OK] nature_of_appointment already exists")

            db.session.commit()
            print("Migration completed successfully.")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Migration failed: {e}")
            return False


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
