"""
Migration script to add sg_lgu_class to salary_grade table.
Run: python migrate_salary_grade_sg_lgu_class.py
"""
import sys
from sqlalchemy import text
from app import create_app, db


def migrate():
    app = create_app()
    with app.app_context():
        try:
            print("Adding sg_lgu_class to salary_grade...")
            inspector = db.inspect(db.engine)
            columns = [col["name"] for col in inspector.get_columns("salary_grade")]

            if "sg_lgu_class" not in columns:
                db.session.execute(text("ALTER TABLE salary_grade ADD COLUMN sg_lgu_class VARCHAR(50)"))
                db.session.commit()
                print("[OK] sg_lgu_class added")
            else:
                print("[OK] sg_lgu_class already exists")

            print("Migration completed successfully.")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Migration failed: {e}")
            return False


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
