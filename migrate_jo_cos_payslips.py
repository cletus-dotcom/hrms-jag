"""
Create jo_cos_payslips table.

  set DATABASE_URL=postgresql://...
  python migrate_jo_cos_payslips.py
"""
from sqlalchemy import inspect

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import JoCosPayslip

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        if 'jo_cos_payslips' not in tables:
            JoCosPayslip.__table__.create(db.engine)
            print('[OK] Table jo_cos_payslips created.')
        else:
            print('[OK] Table jo_cos_payslips already exists.')


if __name__ == '__main__':
    migrate()
