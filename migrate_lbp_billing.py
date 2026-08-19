"""
Create lbp_billing_records and lbp_billing_deductions tables.

  set DATABASE_URL=postgresql://...
  python migrate_lbp_billing.py
"""
from sqlalchemy import inspect

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import LbpBillingRecord, LbpBillingDeduction

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        if 'lbp_billing_records' not in tables:
            LbpBillingRecord.__table__.create(db.engine)
            print('[OK] Table lbp_billing_records created.')
        else:
            print('[OK] Table lbp_billing_records already exists.')
        if 'lbp_billing_deductions' not in tables:
            LbpBillingDeduction.__table__.create(db.engine)
            print('[OK] Table lbp_billing_deductions created.')
        else:
            print('[OK] Table lbp_billing_deductions already exists.')


if __name__ == '__main__':
    migrate()
