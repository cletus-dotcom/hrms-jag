"""
Migration script for online leave application schema.
Creates leave_types, updates leave_requests with new columns, creates leave_balances.
Run against BOTH local DB and Supabase to keep schemas in sync:
  Local:    set DATABASE_URL=postgresql://postgres:password@localhost:5432/hrms && python migrate_leave_schema.py
  Supabase: set DATABASE_URL=<supabase-connection-uri> && python migrate_leave_schema.py
"""
import sys
from sqlalchemy import text
from app import create_app, db


def migrate():
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()

            # 1. Create leave_types if not exists
            if "leave_types" not in tables:
                db.session.execute(text("""
                    CREATE TABLE leave_types (
                        id SERIAL PRIMARY KEY,
                        code VARCHAR(20) NOT NULL UNIQUE,
                        name VARCHAR(100) NOT NULL,
                        description TEXT,
                        is_active BOOLEAN DEFAULT true,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                db.session.commit()
                print("[OK] Table leave_types created.")
            else:
                print("[OK] Table leave_types already exists.")

            # 2. Ensure leave_requests exists and add new columns if missing
            if "leave_requests" not in tables:
                db.session.execute(text("""
                    CREATE TABLE leave_requests (
                        id SERIAL PRIMARY KEY,
                        employee_id INTEGER NOT NULL REFERENCES employees(id),
                        leave_type VARCHAR(50) NOT NULL,
                        start_date DATE NOT NULL,
                        end_date DATE NOT NULL,
                        total_days DECIMAL(5,2),
                        reason TEXT,
                        status VARCHAR(20) DEFAULT 'pending',
                        approved_by INTEGER REFERENCES users(id),
                        approved_at TIMESTAMP,
                        rejection_reason TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                db.session.commit()
                print("[OK] Table leave_requests created.")
            else:
                cols = {c["name"] for c in inspector.get_columns("leave_requests")}
                if "total_days" not in cols:
                    db.session.execute(text("ALTER TABLE leave_requests ADD COLUMN total_days DECIMAL(5,2);"))
                    print("[OK] leave_requests.total_days added.")
                if "rejection_reason" not in cols:
                    db.session.execute(text("ALTER TABLE leave_requests ADD COLUMN rejection_reason TEXT;"))
                    print("[OK] leave_requests.rejection_reason added.")
                if "updated_at" not in cols:
                    db.session.execute(text("ALTER TABLE leave_requests ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;"))
                    print("[OK] leave_requests.updated_at added.")
                db.session.commit()

            # 3. Create leave_balances if not exists
            if "leave_balances" not in tables:
                db.session.execute(text("""
                    CREATE TABLE leave_balances (
                        id SERIAL PRIMARY KEY,
                        employee_id INTEGER NOT NULL REFERENCES employees(id),
                        leave_type VARCHAR(50) NOT NULL,
                        year INTEGER NOT NULL,
                        balance DECIMAL(5,2) NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_leave_balance_emp_type_year UNIQUE (employee_id, leave_type, year)
                    );
                """))
                db.session.commit()
                print("[OK] Table leave_balances created.")
            else:
                print("[OK] Table leave_balances already exists.")

            return True
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Migration failed: {e}")
            return False


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
