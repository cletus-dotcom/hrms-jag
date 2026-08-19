"""
Create withholding_tax_records and withholding_tax_deductions tables.

  set DATABASE_URL=postgresql://...
  python migrate_withholding_tax.py
"""
from sqlalchemy import inspect

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import WithholdingTaxRecord, WithholdingTaxDeduction

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        if 'withholding_tax_records' not in tables:
            WithholdingTaxRecord.__table__.create(db.engine)
            print('[OK] Table withholding_tax_records created.')
        else:
            print('[OK] Table withholding_tax_records already exists.')
        if 'withholding_tax_deductions' not in tables:
            WithholdingTaxDeduction.__table__.create(db.engine)
            print('[OK] Table withholding_tax_deductions created.')
        else:
            print('[OK] Table withholding_tax_deductions already exists.')


if __name__ == '__main__':
    migrate()

