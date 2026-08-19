"""
Create dtr_upload_punches / dtr_upload_punch_flags and ensure assigned_slot exists.

Run:
  python migrate_dtr_upload_punches.py
"""
from sqlalchemy import inspect, text

from app import create_app, db


def migrate():
    app = create_app()
    with app.app_context():
        from app.models import DtrUploadPunch, DtrUploadPunchFlag
        DtrUploadPunch.__table__.create(db.engine, checkfirst=True)
        DtrUploadPunchFlag.__table__.create(db.engine, checkfirst=True)

        inspector = inspect(db.engine)
        cols = {c['name'] for c in inspector.get_columns('dtr_upload_punches')}
        if 'assigned_slot' not in cols:
            db.session.execute(text(
                'ALTER TABLE dtr_upload_punches ADD COLUMN assigned_slot VARCHAR(10)'
            ))
            db.session.commit()
            print('Added dtr_upload_punches.assigned_slot')

        # Backfill last-wins selections so source destination is visible after review UI change
        result = db.session.execute(text(
            """
            UPDATE dtr_upload_punches
            SET assigned_slot = slot
            WHERE is_selected IS TRUE
              AND (assigned_slot IS NULL OR assigned_slot = '')
            """
        ))
        db.session.commit()
        print(
            f'dtr_upload_punches / dtr_upload_punch_flags ready '
            f'(backfilled {result.rowcount} assigned_slot row(s)).'
        )


if __name__ == '__main__':
    migrate()
