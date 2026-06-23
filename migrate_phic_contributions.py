"""
Create phic_contributions table.

  set DATABASE_URL=postgresql://...
  python migrate_phic_contributions.py
"""
from sqlalchemy import inspect

from app import create_app, db


def migrate() -> None:
    app = create_app()
    with app.app_context():
        from app.models import PhicContribution

        inspector = inspect(db.engine)
        if 'phic_contributions' not in inspector.get_table_names():
            PhicContribution.__table__.create(db.engine)
            print('[OK] Table phic_contributions created.')
        else:
            print('[OK] Table phic_contributions already exists.')


if __name__ == '__main__':
    migrate()
