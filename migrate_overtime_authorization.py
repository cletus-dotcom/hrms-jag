"""
Create overtime_authorization table (plantilla OT for DTR regeneration + CTO).

  set DATABASE_URL=postgresql://...
  python migrate_overtime_authorization.py
"""
from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import OvertimeAuthorization

        inspector = db.inspect(db.engine)
        if "overtime_authorization" not in inspector.get_table_names():
            OvertimeAuthorization.__table__.create(db.engine)
            print("[OK] Table overtime_authorization created.")
        else:
            print("[OK] Table overtime_authorization already exists.")


if __name__ == "__main__":
    migrate()
