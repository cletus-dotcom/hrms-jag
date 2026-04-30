"""
Migration script to add agency, lgu_class_level, salary_tranche, salary_grade, salary_step to employees table.
Run: python migrate_employee_salary_fields.py
"""
import sys
from sqlalchemy import text
from app import create_app, db

FIELDS = [
    ("agency", "VARCHAR(50)"),
    ("lgu_class_level", "VARCHAR(20)"),
    ("salary_tranche", "VARCHAR(20)"),
    ("salary_grade", "INTEGER"),
    ("salary_step", "INTEGER"),
]


def migrate():
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            columns = [col["name"] for col in inspector.get_columns("employees")]
            for name, typ in FIELDS:
                if name not in columns:
                    print(f"Adding {name}...")
                    db.session.execute(text(f"ALTER TABLE employees ADD COLUMN {name} {typ}"))
                    print(f"[OK] {name} added")
                else:
                    print(f"[OK] {name} already exists")
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
