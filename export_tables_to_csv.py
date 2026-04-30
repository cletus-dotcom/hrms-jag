"""
Export selected PostgreSQL tables to CSV.

Usage (PowerShell):
  $env:DATABASE_URL="postgresql://postgres:password@localhost:5432/hrms"
  python export_tables_to_csv.py

Outputs:
  exports/departments.csv
  exports/employees.csv
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from app import create_app, db


def _to_csv_scalar(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Keep binary safe in CSV (rare for these tables).
        return bytes(value).hex()
    return value


def export_table_to_csv(
    *,
    table_name: str,
    output_path: Path,
    schema: str | None = None,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    qualified = f"{schema}.{table_name}" if schema else table_name
    sql = text(f'SELECT * FROM "{qualified}"' if schema is None else f'SELECT * FROM "{schema}"."{table_name}"')

    with db.engine.connect() as conn:
        result = conn.execute(sql)
        columns = list(result.keys())

        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()

            row_count = 0
            for row in result.mappings():
                writer.writerow({k: _to_csv_scalar(row.get(k)) for k in columns})
                row_count += 1

    return row_count


def main() -> int:
    # Ensure app config + SQLAlchemy is initialized (reads DATABASE_URL).
    app = create_app()
    export_dir = Path(os.environ.get("HRMS_EXPORT_DIR", "exports"))

    with app.app_context():
        dept_rows = export_table_to_csv(
            table_name="departments",
            output_path=export_dir / "departments.csv",
        )
        emp_rows = export_table_to_csv(
            table_name="employees",
            output_path=export_dir / "employees.csv",
        )

    print(f"Wrote {dept_rows} rows -> {export_dir / 'departments.csv'}")
    print(f"Wrote {emp_rows} rows  -> {export_dir / 'employees.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

