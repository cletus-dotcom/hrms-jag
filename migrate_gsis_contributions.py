"""
Create gsis_contributions table and add month_amount / quincena_amount columns.

  set DATABASE_URL=postgresql://...
  python migrate_gsis_contributions.py
"""
from sqlalchemy import inspect, text

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import GsisContribution

        inspector = inspect(db.engine)
        if 'gsis_contributions' not in inspector.get_table_names():
            GsisContribution.__table__.create(db.engine)
            print('[OK] Table gsis_contributions created.')
            return

        existing = {c['name'] for c in inspector.get_columns('gsis_contributions')}
        alters = []
        if 'month_amount' not in existing:
            alters.append(
                'ALTER TABLE gsis_contributions ADD COLUMN month_amount NUMERIC(12,2) NOT NULL DEFAULT 0'
            )
        if 'quincena_amount' not in existing:
            alters.append(
                'ALTER TABLE gsis_contributions ADD COLUMN quincena_amount NUMERIC(12,2) NOT NULL DEFAULT 0'
            )

        for stmt in alters:
            db.session.execute(text(stmt))
        if alters:
            db.session.execute(text(
                'UPDATE gsis_contributions '
                'SET month_amount = total_amount, '
                'quincena_amount = ROUND(total_amount / 2, 2) '
                'WHERE month_amount = 0 AND total_amount <> 0'
            ))
            db.session.commit()
            print(f'[OK] Applied {len(alters)} column update(s) on gsis_contributions.')
        else:
            print('[OK] Table gsis_contributions already up to date.')


if __name__ == '__main__':
    migrate()
