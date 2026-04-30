"""
Migration script to create employee_appointment_history table.
Run: python migrate_employee_appointment_history.py
"""
import sys
from sqlalchemy import text
from app import create_app, db


def migrate():
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            if "employee_appointment_history" in inspector.get_table_names():
                print("[OK] Table employee_appointment_history already exists.")
                return True
            # Create table (Postgres-compatible)
            sql = """
            CREATE TABLE employee_appointment_history (
                id SERIAL PRIMARY KEY,
                emp_id INTEGER NOT NULL REFERENCES employees(id),
                emp_dept VARCHAR(200),
                emp_position VARCHAR(200),
                appoint_date DATE,
                appoint_status VARCHAR(50),
                appoint_nature VARCHAR(50),
                agency VARCHAR(50),
                lgu_class VARCHAR(20),
                sal_grade INTEGER,
                sal_step INTEGER,
                sal_amount NUMERIC(12, 2),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                location VARCHAR(45),
                user_id INTEGER REFERENCES users(id)
            );
            """
            db.session.execute(text(sql))
            db.session.commit()
            print("Table employee_appointment_history created successfully.")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Migration failed: {e}")
            return False


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
