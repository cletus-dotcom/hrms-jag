"""
Migration script to create daily_time_record table (Civil Service Form No. 48 - DTR).
Run: python migrate_daily_time_record.py
"""
import sys
from sqlalchemy import text
from app import create_app, db


def migrate():
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            if "daily_time_record" in inspector.get_table_names():
                print("[OK] Table daily_time_record already exists.")
                return True
            sql = """
            CREATE TABLE daily_time_record (
                id SERIAL PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                record_date DATE NOT NULL,
                am_in TIME,
                am_out TIME,
                pm_in TIME,
                pm_out TIME,
                undertime_hrs SMALLINT DEFAULT 0,
                undertime_mins SMALLINT DEFAULT 0,
                remarks VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_dtr_employee_date UNIQUE (employee_id, record_date)
            );
            """
            db.session.execute(text(sql))
            db.session.commit()
            print("Table daily_time_record created successfully.")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Migration failed: {e}")
            return False


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
