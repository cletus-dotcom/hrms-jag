"""Create/upgrade event_attendance tables on the configured database."""
from sqlalchemy import text

from app import create_app, db
from app.models import EventAttendance, EventAttendanceBatch

app = create_app()
with app.app_context():
    EventAttendanceBatch.__table__.create(db.engine, checkfirst=True)
    EventAttendance.__table__.create(db.engine, checkfirst=True)
    for stmt in (
        'ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS barangay VARCHAR(120)',
        'ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS municipality VARCHAR(120)',
        'ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS province VARCHAR(120)',
        'ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS location_text VARCHAR(255)',
    ):
        db.session.execute(text(stmt))
    db.session.commit()
    print('OK: event_attendance location columns ready')
