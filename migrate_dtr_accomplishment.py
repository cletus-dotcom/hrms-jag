"""Create dtr_accomplishment_report table for JO/COS My DTR reports."""

from app import create_app, db
from app.models import DtrAccomplishmentReport


def main() -> None:
    app = create_app()
    with app.app_context():
        DtrAccomplishmentReport.__table__.create(db.engine, checkfirst=True)
        print('dtr_accomplishment_report migration complete.')


if __name__ == '__main__':
    main()
