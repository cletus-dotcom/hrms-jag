"""
Migration script for leave_ledger table.
Creates the leave_ledger table for tracking all leave credit transactions.

Run against BOTH local DB and Supabase to keep schemas in sync:
  Local:    set DATABASE_URL=postgresql://postgres:password@localhost:5432/hrms && python migrate_leave_ledger.py
  Supabase: set DATABASE_URL=<supabase-connection-uri> && python migrate_leave_ledger.py
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

            # Create leave_ledger if not exists
            if "leave_ledger" not in tables:
                db.session.execute(text("""
                    CREATE TABLE leave_ledger (
                        id SERIAL PRIMARY KEY,
                        employee_id INTEGER NOT NULL REFERENCES employees(id),
                        transaction_date DATE NOT NULL,
                        particulars VARCHAR(500) NOT NULL,
                        
                        -- Vacation Leave columns
                        vl_earned NUMERIC(6,3) DEFAULT 0,
                        vl_applied NUMERIC(6,3) DEFAULT 0,
                        vl_tardiness NUMERIC(6,3) DEFAULT 0,
                        vl_undertime NUMERIC(6,3) DEFAULT 0,
                        vl_balance NUMERIC(8,3) DEFAULT 0,
                        
                        -- Sick Leave columns
                        sl_earned NUMERIC(6,3) DEFAULT 0,
                        sl_applied NUMERIC(6,3) DEFAULT 0,
                        sl_balance NUMERIC(8,3) DEFAULT 0,
                        
                        -- Special Privilege Leave
                        spl_earned NUMERIC(6,3) DEFAULT 0,
                        spl_used NUMERIC(6,3) DEFAULT 0,
                        spl_balance NUMERIC(6,3) DEFAULT 0,
                        
                        -- Wellness Leave
                        wl_earned NUMERIC(6,3) DEFAULT 0,
                        wl_used NUMERIC(6,3) DEFAULT 0,
                        wl_balance NUMERIC(6,3) DEFAULT 0,
                        
                        -- Other Leaves (balance only)
                        ml_credits NUMERIC(6,3) DEFAULT 0,
                        ml_used NUMERIC(6,3) DEFAULT 0,
                        ml_balance NUMERIC(6,3) DEFAULT 0,
                        pl_credits NUMERIC(6,3) DEFAULT 0,
                        pl_used NUMERIC(6,3) DEFAULT 0,
                        pl_balance NUMERIC(6,3) DEFAULT 0,
                        sp_credits NUMERIC(6,3) DEFAULT 0,
                        sp_used NUMERIC(6,3) DEFAULT 0,
                        sp_balance NUMERIC(6,3) DEFAULT 0,
                        avaw_credits NUMERIC(6,3) DEFAULT 0,
                        avaw_used NUMERIC(6,3) DEFAULT 0,
                        avaw_balance NUMERIC(6,3) DEFAULT 0,
                        study_credits NUMERIC(6,3) DEFAULT 0,
                        study_used NUMERIC(6,3) DEFAULT 0,
                        study_balance NUMERIC(6,3) DEFAULT 0,
                        rehab_credits NUMERIC(6,3) DEFAULT 0,
                        rehab_used NUMERIC(6,3) DEFAULT 0,
                        rehab_balance NUMERIC(6,3) DEFAULT 0,
                        slbw_credits NUMERIC(6,3) DEFAULT 0,
                        slbw_used NUMERIC(6,3) DEFAULT 0,
                        slbw_balance NUMERIC(6,3) DEFAULT 0,
                        se_calamity_credits NUMERIC(6,3) DEFAULT 0,
                        se_calamity_used NUMERIC(6,3) DEFAULT 0,
                        se_calamity_balance NUMERIC(6,3) DEFAULT 0,
                        adopt_credits NUMERIC(6,3) DEFAULT 0,
                        adopt_used NUMERIC(6,3) DEFAULT 0,
                        adopt_balance NUMERIC(6,3) DEFAULT 0,
                        
                        -- CTO (Credit to Overtime)
                        cto_earned NUMERIC(6,3) DEFAULT 0,
                        cto_used NUMERIC(6,3) DEFAULT 0,
                        cto_balance NUMERIC(6,3) DEFAULT 0,
                        
                        -- Remarks (Approved, Dis-approved, Cancelled, or custom)
                        remarks VARCHAR(500),
                        
                        -- Metadata
                        created_by INTEGER REFERENCES users(id),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                db.session.commit()
                print("[OK] Table leave_ledger created.")

                # Create index for faster lookups by employee
                db.session.execute(text("""
                    CREATE INDEX idx_leave_ledger_employee_date 
                    ON leave_ledger(employee_id, transaction_date);
                """))
                db.session.commit()
                print("[OK] Index idx_leave_ledger_employee_date created.")
            else:
                print("[OK] Table leave_ledger already exists.")
                # Check for any missing columns and add them
                cols = {c["name"] for c in inspector.get_columns("leave_ledger")}
                
                new_columns = [
                    ("vl_earned", "NUMERIC(6,3) DEFAULT 0"),
                    ("vl_applied", "NUMERIC(6,3) DEFAULT 0"),
                    ("vl_tardiness", "NUMERIC(6,3) DEFAULT 0"),
                    ("vl_undertime", "NUMERIC(6,3) DEFAULT 0"),
                    ("vl_balance", "NUMERIC(8,3) DEFAULT 0"),
                    ("sl_earned", "NUMERIC(6,3) DEFAULT 0"),
                    ("sl_applied", "NUMERIC(6,3) DEFAULT 0"),
                    ("sl_balance", "NUMERIC(8,3) DEFAULT 0"),
                    ("spl_earned", "NUMERIC(6,3) DEFAULT 0"),
                    ("spl_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("spl_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("wl_earned", "NUMERIC(6,3) DEFAULT 0"),
                    ("wl_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("wl_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("ml_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("ml_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("ml_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("pl_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("pl_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("pl_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("sp_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("sp_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("sp_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("avaw_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("avaw_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("avaw_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("study_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("study_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("study_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("rehab_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("rehab_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("rehab_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("slbw_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("slbw_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("slbw_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("se_calamity_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("se_calamity_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("se_calamity_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("adopt_credits", "NUMERIC(6,3) DEFAULT 0"),
                    ("adopt_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("adopt_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("cto_earned", "NUMERIC(6,3) DEFAULT 0"),
                    ("cto_used", "NUMERIC(6,3) DEFAULT 0"),
                    ("cto_balance", "NUMERIC(6,3) DEFAULT 0"),
                    ("remarks", "VARCHAR(500)"),
                ]
                
                for col_name, col_def in new_columns:
                    if col_name not in cols:
                        db.session.execute(text(f"ALTER TABLE leave_ledger ADD COLUMN {col_name} {col_def};"))
                        print(f"[OK] leave_ledger.{col_name} added.")
                
                db.session.commit()

            return True
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Migration failed: {e}")
            return False


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
