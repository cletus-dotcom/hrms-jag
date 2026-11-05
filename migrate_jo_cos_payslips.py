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
        from sqlalchemy import text
        inspector = inspect(db.engine)
        cols = {c['name'] for c in inspector.get_columns('jo_cos_payslips')}
        if 'fund_release' not in cols:
            db.session.execute(text(
                "ALTER TABLE jo_cos_payslips ADD COLUMN IF NOT EXISTS "
                "fund_release VARCHAR(20) DEFAULT 'ATM' NOT NULL"
            ))
            db.session.commit()
            print('[OK] Column jo_cos_payslips.fund_release added.')


if __name__ == '__main__':
    migrate()
