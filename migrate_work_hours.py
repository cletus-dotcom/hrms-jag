"""
Create work_hours table (daily worktime history from DTR quincena regeneration).

  set DATABASE_URL=postgresql://...
  python migrate_work_hours.py
"""
from sqlalchemy import text

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import WorkHours

        inspector = db.inspect(db.engine)
        if "work_hours" not in inspector.get_table_names():
            WorkHours.__table__.create(db.engine)
            print("[OK] Table work_hours created.")
        else:
            print("[OK] Table work_hours already exists.")


if __name__ == "__main__":
    migrate()
