"""eRMT service helpers — serialize, search, stats."""

from sqlalchemy import and_, func, or_

from app import db
from app.ermt_helpers import display_ermt_record_type, network_path_to_open_href
from app.models import ErmtRecord


def serialize_ermt_record(record, include_details=False):
    pdf_link = record.pdf_link or ''
    payload = {
        'id': record.id,
        'record_number': record.record_number,
        'title': record.title or '',
        'record_type': display_ermt_record_type(record.record_type, record.record_type_part),
        'record_date': record.record_date.strftime('%Y-%m-%d') if record.record_date else '',
        'cabinet_number': record.cabinet_number or '',
        'shelf_number': record.shelf_number or '',
        'pdf_link': pdf_link,
        'pdf_open_href': network_path_to_open_href(pdf_link),
        'has_pdf': bool(pdf_link),
        'encoded_by': record.encoded_by or '',
        'date_encoded': record.date_encoded.strftime('%Y-%m-%d %H:%M') if record.date_encoded else '',
    }
    if include_details:
        payload.update({
            'keywords': record.keywords or '',
            'remarks': record.remarks or '',
            'date_updated': record.date_updated.strftime('%Y-%m-%d %H:%M') if record.date_updated else '',
        })
    return payload


def records_query():
    return ErmtRecord.query.order_by(
        ErmtRecord.record_date.desc(),
        ErmtRecord.record_number.desc(),
    )


def search_records_query(term):
    query = records_query()
    if not term:
        return query
    like = f'%{term.strip()}%'
    return query.filter(
        or_(
            ErmtRecord.record_number.ilike(like),
            ErmtRecord.title.ilike(like),
            ErmtRecord.record_type.ilike(like),
            ErmtRecord.record_type_part.ilike(like),
            ErmtRecord.cabinet_number.ilike(like),
            ErmtRecord.shelf_number.ilike(like),
            ErmtRecord.keywords.ilike(like),
            ErmtRecord.remarks.ilike(like),
            ErmtRecord.encoded_by.ilike(like),
            ErmtRecord.pdf_link.ilike(like),
        )
    )


def ermt_dashboard_stats():
    total_records = db.session.query(func.count(ErmtRecord.id)).scalar() or 0
    total_cabinets = (
        db.session.query(func.count(func.distinct(ErmtRecord.cabinet_number)))
        .filter(
            ErmtRecord.cabinet_number.isnot(None),
            ErmtRecord.cabinet_number != '',
        )
        .scalar()
        or 0
    )
    with_pdf = (
        db.session.query(func.count(ErmtRecord.id))
        .filter(
            and_(ErmtRecord.pdf_link.isnot(None), ErmtRecord.pdf_link != ''),
        )
        .scalar()
        or 0
    )
    return {
        'total_records': total_records,
        'total_cabinets': total_cabinets,
        'with_pdf': with_pdf,
    }
