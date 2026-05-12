"""
Create jo_cos_designation table, add employees.jo_cos_designation_id, and optionally load rows from Excel.

  set DATABASE_URL=postgresql://user:pass@localhost:5432/hrms
  python migrate_jo_cos_designation.py "c:\\path\\to\\jo_cos_designation.xlsx"

If the table already has rows, data import is skipped (use --replace to truncate and reload).

Default Windows path (override with first CLI arg or JO_COS_DESIGNATION_XLSX):
  c:\\Users\\jagna\\MICT\\IT_projects\\System\\hrmo\\jo_cos_designation.xlsx
"""
import argparse
import os
import sys
from typing import Optional

from sqlalchemy import text

from app import create_app, db


DEFAULT_XLSX = r"c:\Users\jagna\MICT\IT_projects\System\hrmo\jo_cos_designation.xlsx"


def _normalize_designation(raw) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s or s.lower() == "nan":
        return None
    return " ".join(s.split())


def migrate(xlsx_path: Optional[str], replace: bool) -> None:
    app = create_app()
    with app.app_context():
        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()

        if "jo_cos_designation" not in tables:
            db.session.execute(
                text(
                    """
                    CREATE TABLE jo_cos_designation (
                        id SERIAL PRIMARY KEY,
                        designation VARCHAR(500) NOT NULL UNIQUE,
                        sort_order INTEGER NOT NULL DEFAULT 0
                    );
                    """
                )
            )
            db.session.commit()
            print("[OK] Table jo_cos_designation created.")
        else:
            print("[OK] Table jo_cos_designation already exists.")

        inspector = db.inspect(db.engine)
        if "employees" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("employees")}
            if "jo_cos_designation_id" not in cols:
                db.session.execute(
                    text(
                        """
                        ALTER TABLE employees
                        ADD COLUMN jo_cos_designation_id INTEGER
                        REFERENCES jo_cos_designation(id) ON DELETE SET NULL;
                        """
                    )
                )
                db.session.commit()
                print("[OK] employees.jo_cos_designation_id added.")
            else:
                print("[OK] employees.jo_cos_designation_id already exists.")

        from app.models import JoCosDesignation

        count = db.session.execute(text("SELECT COUNT(*) FROM jo_cos_designation")).scalar() or 0
        if replace and count:
            db.session.execute(text("UPDATE employees SET jo_cos_designation_id = NULL"))
            db.session.execute(text("DELETE FROM jo_cos_designation"))
            db.session.commit()
            print("[OK] jo_cos_designation truncated (--replace).")
            count = 0

        path = xlsx_path or os.environ.get("JO_COS_DESIGNATION_XLSX") or DEFAULT_XLSX
        if not os.path.isfile(path):
            print(f"[SKIP] Excel not found: {path} — table ready; import when file is available.")
            return

        if count:
            print(f"[SKIP] jo_cos_designation already has {count} row(s). Use --replace to reload from Excel.")
            return

        import pandas as pd

        df = pd.read_excel(path)
        if "DESIGNATION" not in df.columns:
            print("[ERROR] Excel must contain a DESIGNATION column.", file=sys.stderr)
            sys.exit(1)

        rows = []
        for i, raw in enumerate(df["DESIGNATION"].tolist()):
            des = _normalize_designation(raw)
            if des:
                rows.append({"designation": des, "sort_order": len(rows)})

        for r in rows:
            db.session.add(JoCosDesignation(designation=r["designation"], sort_order=r["sort_order"]))
        db.session.commit()
        print(f"[OK] Inserted {len(rows)} designation(s) from {path}")


def main():
    p = argparse.ArgumentParser(description="Migrate jo_cos_designation + optional Excel import")
    p.add_argument("xlsx", nargs="?", default=None, help="Path to jo_cos_designation.xlsx")
    p.add_argument("--replace", action="store_true", help="Truncate table and reload from Excel")
    args = p.parse_args()
    migrate(args.xlsx, args.replace)


if __name__ == "__main__":
    main()
