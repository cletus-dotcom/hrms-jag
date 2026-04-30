"""
Create leave_ledger_deletion audit table (snapshots of deleted leave_ledger rows).

  set DATABASE_URL=... && python migrate_leave_ledger_deletion.py
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
            if "leave_ledger_deletion" in tables:
                print("[OK] Table leave_ledger_deletion already exists.")
                return 0
            db.session.execute(text("""
                CREATE TABLE leave_ledger_deletion (
                    id SERIAL PRIMARY KEY,
                    original_ledger_id INTEGER NOT NULL,
                    employee_id INTEGER NOT NULL,
                    deleted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    deleted_by_user_id INTEGER REFERENCES users(id),
                    deleted_by_username VARCHAR(80),
                    delete_source VARCHAR(40) NOT NULL,
                    transaction_date DATE NOT NULL,
                    particulars VARCHAR(500) NOT NULL,
                    vl_earned NUMERIC(6,3) DEFAULT 0,
                    vl_applied NUMERIC(6,3) DEFAULT 0,
                    vl_tardiness NUMERIC(6,3) DEFAULT 0,
                    vl_undertime NUMERIC(6,3) DEFAULT 0,
                    vl_balance NUMERIC(8,3) DEFAULT 0,
                    sl_earned NUMERIC(6,3) DEFAULT 0,
                    sl_applied NUMERIC(6,3) DEFAULT 0,
                    sl_balance NUMERIC(8,3) DEFAULT 0,
                    spl_earned NUMERIC(6,3) DEFAULT 0,
                    spl_used NUMERIC(6,3) DEFAULT 0,
                    spl_balance NUMERIC(6,3) DEFAULT 0,
                    wl_earned NUMERIC(6,3) DEFAULT 0,
                    wl_used NUMERIC(6,3) DEFAULT 0,
                    wl_balance NUMERIC(6,3) DEFAULT 0,
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
                    cto_earned NUMERIC(6,3) DEFAULT 0,
                    cto_used NUMERIC(6,3) DEFAULT 0,
                    cto_balance NUMERIC(6,3) DEFAULT 0,
                    remarks VARCHAR(500),
                    orig_created_by INTEGER,
                    orig_created_at TIMESTAMP
                );
            """))
            db.session.execute(text("""
                CREATE INDEX idx_leave_ledger_deletion_employee_deleted
                ON leave_ledger_deletion(employee_id, deleted_at DESC);
            """))
            db.session.commit()
            print("[OK] Table leave_ledger_deletion created.")
            return 0
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] {e}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(migrate())
