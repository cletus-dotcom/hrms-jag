"""
Allow employees.user_id to be NULL (employee can exist without a user account).
Run after backup/restore or when you see: null value in column "user_id" violates not-null constraint
"""
import sys
from sqlalchemy import text
from app import create_app, db


def migrate():
    app = create_app()
    with app.app_context():
        try:
            print("Allowing NULL for employees.user_id...")
            db.session.execute(text("ALTER TABLE employees ALTER COLUMN user_id DROP NOT NULL"))
            db.session.commit()
            print("Done. employees.user_id can now be NULL.")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            return False


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
