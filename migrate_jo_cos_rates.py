"""
Create jo_cos_rate table and optionally load rows from CSV.

  set DATABASE_URL=postgresql://...
  python migrate_jo_cos_rates.py "c:\\path\\to\\jo_cos_rates.csv"
  python migrate_jo_cos_rates.py --replace

Default CSV (override with first arg or JO_COS_RATES_CSV):
  c:\\Users\\jagna\\MICT\\IT_projects\\System\\hrmo\\jo_cos_rates.csv

CSV columns: id, status_of_appointment, jo_cos_designation, rate_per_day
"""
import argparse
import os
import sys
from decimal import Decimal
from typing import Optional

from sqlalchemy import text

from app import create_app, db

DEFAULT_CSV = r"c:\Users\jagna\MICT\IT_projects\System\hrmo\jo_cos_rates.csv"
ALLOWED_STATUS = {'Job Order', 'Contract of Service'}


def _norm_label(raw) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).replace('\r\n', '\n').replace('\r', '\n').strip()
    if not s or s.lower() == 'nan':
        return None
    return ' '.join(s.split())


def migrate(csv_path: Optional[str], replace: bool) -> None:
    app = create_app()
    with app.app_context():
        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()

        if 'jo_cos_rate' not in tables:
            db.session.execute(
                text(
                    """
                    CREATE TABLE jo_cos_rate (
                        id SERIAL PRIMARY KEY,
                        status_of_appointment VARCHAR(50) NOT NULL,
                        designation_label VARCHAR(500) NOT NULL,
                        rate_per_day NUMERIC(14, 6) NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        CONSTRAINT uq_jo_cos_rate_status_designation
                            UNIQUE (status_of_appointment, designation_label)
                    );
                    """
                )
            )
            db.session.commit()
            print('[OK] Table jo_cos_rate created.')
        else:
            print('[OK] Table jo_cos_rate already exists.')

        from app.models import JoCosRate

        count = db.session.execute(text('SELECT COUNT(*) FROM jo_cos_rate')).scalar() or 0
        if replace and count:
            db.session.execute(text('DELETE FROM jo_cos_rate'))
            db.session.commit()
            print('[OK] jo_cos_rate truncated (--replace).')
            count = 0

        path = csv_path or os.environ.get('JO_COS_RATES_CSV') or DEFAULT_CSV
        if not os.path.isfile(path):
            print(f'[SKIP] CSV not found: {path} — table ready; import when file is available.')
            return

        if count:
            print(f'[SKIP] jo_cos_rate already has {count} row(s). Use --replace to reload from CSV.')
            return

        import csv as csv_mod

        by_key = {}
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv_mod.DictReader(f)
            need = {'status_of_appointment', 'jo_cos_designation', 'rate_per_day'}
            if not reader.fieldnames or not need.issubset({(c or '').strip() for c in reader.fieldnames}):
                print('[ERROR] CSV must have columns: status_of_appointment, jo_cos_designation, rate_per_day', file=sys.stderr)
                sys.exit(1)
            for row in reader:
                st = (row.get('status_of_appointment') or '').strip()
                lab = _norm_label(row.get('jo_cos_designation'))
                raw_rate = row.get('rate_per_day')
                if st not in ALLOWED_STATUS or not lab:
                    continue
                try:
                    rate = Decimal(str(raw_rate).strip().replace(',', ''))
                except Exception:
                    continue
                if rate <= 0:
                    continue
                by_key[(st, lab)] = (st, lab, rate)
        rows_in = list(by_key.values())

        for i, (st, lab, rate) in enumerate(rows_in):
            db.session.add(
                JoCosRate(
                    status_of_appointment=st,
                    designation_label=lab,
                    rate_per_day=rate,
                    sort_order=i,
                )
            )
        db.session.commit()
        print(f'[OK] Inserted {len(rows_in)} rate(s) from {path}')


def main():
    p = argparse.ArgumentParser(description='Migrate jo_cos_rate + optional CSV import')
    p.add_argument('csv', nargs='?', default=None, help='Path to jo_cos_rates.csv')
    p.add_argument('--replace', action='store_true', help='Truncate table and reload from CSV')
    args = p.parse_args()
    migrate(args.csv, args.replace)


if __name__ == '__main__':
    main()
