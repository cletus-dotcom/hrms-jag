"""
Create flexible_worktime table (quincena flexi assignments for DTR regeneration).

  set DATABASE_URL=postgresql://...
  python migrate_flexible_worktime.py
"""
from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import FlexibleWorktime

        inspector = db.inspect(db.engine)
        if "flexible_worktime" not in inspector.get_table_names():
            FlexibleWorktime.__table__.create(db.engine)
            print("[OK] Table flexible_worktime created.")
        else:
            print("[OK] Table flexible_worktime already exists.")


if __name__ == "__main__":
    migrate()
