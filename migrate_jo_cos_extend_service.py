"""
Create jo_cos_extend_service and jo_cos_overtime tables.

  set DATABASE_URL=postgresql://...
  python migrate_jo_cos_extend_service.py
"""
from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import JoCosExtendService, JoCosOvertime

        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()
        if "jo_cos_extend_service" not in tables:
            JoCosExtendService.__table__.create(db.engine)
            print("[OK] Table jo_cos_extend_service created.")
        else:
            print("[OK] Table jo_cos_extend_service already exists.")
        if "jo_cos_overtime" not in tables:
            JoCosOvertime.__table__.create(db.engine)
            print("[OK] Table jo_cos_overtime created.")
        else:
            print("[OK] Table jo_cos_overtime already exists.")


if __name__ == "__main__":
    migrate()
