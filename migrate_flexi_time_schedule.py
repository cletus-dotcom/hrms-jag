"""
Create flexi_time_schedule table and optionally load rows from CSV.

  set DATABASE_URL=postgresql://...
  python migrate_flexi_time_schedule.py "c:\\path\\to\\flexi-time.csv"
  python migrate_flexi_time_schedule.py --replace

Default CSV (override with first arg or FLEXI_TIME_CSV):
  c:\\Users\\jagna\\MICT\\IT_projects\\System\\hrmo\\flexi-time.csv

CSV columns: id, shift_code, time_in, time_out
"""
import argparse
import os
import sys
from typing import Optional

from sqlalchemy import text

from app import create_app, db

DEFAULT_CSV = r"c:\Users\jagna\MICT\IT_projects\System\hrmo\flexi-time.csv"


def _norm(raw) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s or s.lower() == "nan":
        return None
    return " ".join(s.split())


def migrate(csv_path: Optional[str], replace: bool) -> None:
    app = create_app()
    with app.app_context():
        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()

        if "flexi_time_schedule" not in tables:
            db.session.execute(
                text(
                    """
                    CREATE TABLE flexi_time_schedule (
                        id SERIAL PRIMARY KEY,
                        shift_code VARCHAR(64) NOT NULL,
                        time_in VARCHAR(64) NOT NULL,
                        time_out VARCHAR(64) NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        CONSTRAINT uq_flexi_time_schedule_shift_code UNIQUE (shift_code)
                    );
                    """
                )
            )
            db.session.commit()
            print("[OK] Table flexi_time_schedule created.")
        else:
            print("[OK] Table flexi_time_schedule already exists.")

        from app.models import FlexiTimeSchedule

        count = db.session.execute(text("SELECT COUNT(*) FROM flexi_time_schedule")).scalar() or 0
        if replace and count:
            db.session.execute(text("DELETE FROM flexi_time_schedule"))
            db.session.commit()
            print("[OK] flexi_time_schedule truncated (--replace).")
            count = 0

        path = csv_path or os.environ.get("FLEXI_TIME_CSV") or DEFAULT_CSV
        if not os.path.isfile(path):
            print(f"[SKIP] CSV not found: {path} — table ready; import when file is available.")
            return

        if count:
            print(
                f"[SKIP] flexi_time_schedule already has {count} row(s). Use --replace to reload from CSV."
            )
            return

        import csv as csv_mod

        by_code = {}
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv_mod.DictReader(f)
            need = {"shift_code", "time_in", "time_out"}
            cols = {(c or "").strip() for c in (reader.fieldnames or [])}
            if not reader.fieldnames or not need.issubset(cols):
                print(
                    "[ERROR] CSV must have columns: shift_code, time_in, time_out",
                    file=sys.stderr,
                )
                sys.exit(1)
            for row in reader:
                code = _norm(row.get("shift_code"))
                tin = _norm(row.get("time_in"))
                tout = _norm(row.get("time_out"))
                if not code or not tin or not tout:
                    continue
                if len(code) > 64 or len(tin) > 64 or len(tout) > 64:
                    continue
                by_code[code] = (code, tin, tout)
        rows_in = list(by_code.values())

        for i, (code, tin, tout) in enumerate(rows_in):
            db.session.add(
                FlexiTimeSchedule(shift_code=code, time_in=tin, time_out=tout, sort_order=i)
            )
        db.session.commit()
        print(f"[OK] Inserted {len(rows_in)} row(s) from {path}")


def main():
    p = argparse.ArgumentParser(description="Migrate flexi_time_schedule + optional CSV import")
    p.add_argument("csv", nargs="?", default=None, help="Path to flexi-time.csv")
    p.add_argument("--replace", action="store_true", help="Truncate table and reload from CSV")
    args = p.parse_args()
    migrate(args.csv, args.replace)


if __name__ == "__main__":
    main()
