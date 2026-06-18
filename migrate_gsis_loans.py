"""
Create gsis_loan_records and gsis_loan_deductions tables.

  set DATABASE_URL=postgresql://...
  python migrate_gsis_loans.py
"""
from sqlalchemy import inspect

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import GsisLoanRecord, GsisLoanDeduction

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        created = 0
        if 'gsis_loan_records' not in tables:
            GsisLoanRecord.__table__.create(db.engine)
            created += 1
            print('[OK] Table gsis_loan_records created.')
        else:
            print('[OK] Table gsis_loan_records already exists.')
        if 'gsis_loan_deductions' not in tables:
            GsisLoanDeduction.__table__.create(db.engine)
            created += 1
            print('[OK] Table gsis_loan_deductions created.')
        else:
            print('[OK] Table gsis_loan_deductions already exists.')
        if not created:
            print('[OK] GSIS loan tables already up to date.')


if __name__ == '__main__':
    migrate()
