"""
Create HDMF (Pag-IBIG) deduction tables.

  set DATABASE_URL=postgresql://...
  python migrate_hdmf.py
"""
from sqlalchemy import inspect, text

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import (
            HdmfContributionDeduction,
            HdmfContributionRecord,
            HdmfLoanDeduction,
            HdmfLoanRecord,
        )

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        models = (
            ('hdmf_contribution_records', HdmfContributionRecord),
            ('hdmf_contribution_deductions', HdmfContributionDeduction),
            ('hdmf_loan_records', HdmfLoanRecord),
            ('hdmf_loan_deductions', HdmfLoanDeduction),
        )
        created = 0
        for name, model in models:
            if name not in tables:
                model.__table__.create(db.engine)
                created += 1
                print(f'[OK] Table {name} created.')
            else:
                print(f'[OK] Table {name} already exists.')

        # Backfill schema changes (safe ALTERs)
        if 'hdmf_contribution_records' in tables:
            cols = {c['name'] for c in inspector.get_columns('hdmf_contribution_records')}
            if 'classification' not in cols:
                db.session.execute(text("ALTER TABLE hdmf_contribution_records ADD COLUMN classification VARCHAR(64);"))
                db.session.commit()
                print('[OK] Table hdmf_contribution_records altered (classification).')

        if 'hdmf_contribution_deductions' in tables:
            cols = {c['name'] for c in inspector.get_columns('hdmf_contribution_deductions')}
            if 'classification' not in cols:
                db.session.execute(text("ALTER TABLE hdmf_contribution_deductions ADD COLUMN classification VARCHAR(64);"))
                db.session.commit()
                print('[OK] Table hdmf_contribution_deductions altered (classification).')

        if 'hdmf_loan_records' in tables:
            cols = {c['name'] for c in inspector.get_columns('hdmf_loan_records')}
            if 'classification' not in cols:
                db.session.execute(text("ALTER TABLE hdmf_loan_records ADD COLUMN classification VARCHAR(64);"))
                db.session.commit()
                print('[OK] Table hdmf_loan_records altered (classification).')

        if 'hdmf_loan_deductions' in tables:
            cols = {c['name'] for c in inspector.get_columns('hdmf_loan_deductions')}
            alters = []
            if 'classification' not in cols:
                alters.append("ADD COLUMN classification VARCHAR(64)")
            if 'q1_amount' not in cols:
                alters.append("ADD COLUMN q1_amount NUMERIC(12,2) NOT NULL DEFAULT 0")
            if 'q2_amount' not in cols:
                alters.append("ADD COLUMN q2_amount NUMERIC(12,2) NOT NULL DEFAULT 0")
            if 'q1_enabled' not in cols:
                alters.append("ADD COLUMN q1_enabled BOOLEAN NOT NULL DEFAULT TRUE")
            if 'q2_enabled' not in cols:
                alters.append("ADD COLUMN q2_enabled BOOLEAN NOT NULL DEFAULT TRUE")
            for stmt in alters:
                db.session.execute(text(f"ALTER TABLE hdmf_loan_deductions {stmt};"))
            if alters:
                db.session.commit()
                print('[OK] Table hdmf_loan_deductions altered (quincena columns).')
        if not created:
            print('[OK] HDMF tables already up to date.')


if __name__ == '__main__':
    migrate()
