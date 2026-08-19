"""
Create jofemco_billing_records and jofemco_billing_deductions tables.

  set DATABASE_URL=postgresql://...
  python migrate_jofemco_billing.py
"""
from sqlalchemy import inspect

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import JofemcoBillingRecord, JofemcoBillingDeduction

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        if 'jofemco_billing_records' not in tables:
            JofemcoBillingRecord.__table__.create(db.engine)
            print('[OK] Table jofemco_billing_records created.')
        else:
            print('[OK] Table jofemco_billing_records already exists.')
        if 'jofemco_billing_deductions' not in tables:
            JofemcoBillingDeduction.__table__.create(db.engine)
            print('[OK] Table jofemco_billing_deductions created.')
        else:
            print('[OK] Table jofemco_billing_deductions already exists.')


if __name__ == '__main__':
    migrate()
