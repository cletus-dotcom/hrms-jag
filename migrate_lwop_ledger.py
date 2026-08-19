"""
Add LWOP columns to leave_ledger and leave_ledger_deletion.

Run:
  python migrate_lwop_ledger.py
"""
from sqlalchemy import inspect, text

from app import create_app, db


def migrate():
    from sqlalchemy import inspect

    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        for table in ('leave_ledger', 'leave_ledger_deletion'):
            if table not in inspector.get_table_names():
                print(f'Skip {table}: table not found')
                continue
            cols = {c['name'] for c in inspector.get_columns(table)}
            adds = []
            if 'lwop_days' not in cols:
                adds.append(('lwop_days', 'NUMERIC(6,3) DEFAULT 0'))
            if 'lwop_balance' not in cols:
                adds.append(('lwop_balance', 'NUMERIC(8,3) DEFAULT 0'))
            if 'lwop_amount' not in cols:
                adds.append(('lwop_amount', 'NUMERIC(12,2) DEFAULT 0'))
            for name, typ in adds:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {typ}'))
                print(f'Added {table}.{name}')
            db.session.commit()
        print('LWOP ledger migration complete.')


if __name__ == '__main__':
    migrate()
