"""
Reset PostgreSQL sequences tied to table columns (SERIAL / IDENTITY).

Runs sql/reset_sequences_postgres.sql using the same DATABASE_URL as the app.

  set DATABASE_URL=postgresql://postgres:password@localhost:5432/hrms
  python reset_sequences.py

If `psql` is not on your PATH (typical on Windows without client tools), use this
script instead of invoking psql directly.
"""
from pathlib import Path

from app import create_app, db


def main() -> None:
    root = Path(__file__).resolve().parent
    sql_path = root / "sql" / "reset_sequences_postgres.sql"
    if not sql_path.is_file():
        raise SystemExit(f"Missing SQL file: {sql_path}")

    sql = sql_path.read_text(encoding="utf-8")
    app = create_app()
    with app.app_context():
        # Raw DBAPI avoids SQLAlchemy text() treating `%` inside the DO block as escapes.
        conn = db.engine.raw_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
        finally:
            conn.close()
    print(f"[OK] Executed {sql_path.name}; check server logs for NOTICE lines.")


if __name__ == "__main__":
    main()
