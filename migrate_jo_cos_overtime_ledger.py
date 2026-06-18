"""
Create jo_cos_overtime_ledger, jo_cos_overtime_offset_request, hrms_notifications tables.

  set DATABASE_URL=postgresql://...
  python migrate_jo_cos_overtime_ledger.py
"""
from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import JoCosOvertimeLedger, JoCosOvertimeOffsetRequest, HrmsNotification

        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()
        for name, model in (
            ('jo_cos_overtime_offset_request', JoCosOvertimeOffsetRequest),
            ('jo_cos_overtime_ledger', JoCosOvertimeLedger),
            ('hrms_notifications', HrmsNotification),
        ):
            if name not in tables:
                model.__table__.create(db.engine)
                print(f"[OK] Table {name} created.")
            else:
                print(f"[OK] Table {name} already exists.")


if __name__ == "__main__":
    migrate()
