"""
Create plantilla_payslips table.

Usage:
  python migrate_plantilla_payslips.py
"""

import sys

from app import create_app, db


def migrate_plantilla_payslips():
    app = create_app()
    with app.app_context():
        try:
            from app.models import PlantillaPayslip

            tables = db.inspect(db.engine).get_table_names()
            if 'plantilla_payslips' not in tables:
                PlantillaPayslip.__table__.create(db.engine)
                print('[OK] Table plantilla_payslips created.')
            else:
                print('[OK] Table plantilla_payslips already exists.')
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            print(f'[ERROR] Migration failed: {e}')
            return False


if __name__ == '__main__':
    ok = migrate_plantilla_payslips()
    sys.exit(0 if ok else 1)

