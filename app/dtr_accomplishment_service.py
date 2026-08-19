"""JO/COS accomplishment reports on My DTR."""

from __future__ import annotations

from datetime import date

from app import db
from app.models import DtrAccomplishmentReport, DtrJustification


def accomplishment_map_for_employee(employee_id: int, start_d: date, end_d: date) -> dict[date, str]:
    rows = (
        DtrAccomplishmentReport.query.filter(
            DtrAccomplishmentReport.employee_id == employee_id,
            DtrAccomplishmentReport.record_date >= start_d,
            DtrAccomplishmentReport.record_date <= end_d,
        ).all()
    )
    return {r.record_date: (r.report_text or '') for r in rows}


def enrich_jo_cos_dtr_rows(employee_id: int, rows: list[dict], start_d: date, end_d: date) -> None:
    acc_map = accomplishment_map_for_employee(employee_id, start_d, end_d)
    for row in rows:
        d = row['date']
        row['accomplishment_text'] = acc_map.get(d, '')
        status = (row.get('justification_status') or '').strip().lower()
        row['justification_approved'] = status == 'approved'


def save_accomplishment_reports(
    employee_id: int,
    reports_by_date: dict[date, str],
    *,
    updated_by_user_id: int | None,
) -> int:
    saved = 0
    for record_date, text in reports_by_date.items():
        cleaned = (text or '').strip()
        existing = DtrAccomplishmentReport.query.filter_by(
            employee_id=employee_id,
            record_date=record_date,
        ).first()
        if not cleaned:
            if existing:
                db.session.delete(existing)
                saved += 1
            continue
        if existing:
            existing.report_text = cleaned
            existing.updated_by_user_id = updated_by_user_id
        else:
            db.session.add(DtrAccomplishmentReport(
                employee_id=employee_id,
                record_date=record_date,
                report_text=cleaned,
                updated_by_user_id=updated_by_user_id,
            ))
        saved += 1
    return saved


def approved_accomplishment_export_rows(employee_id: int, rows: list[dict]) -> list[dict]:
    """Rows with approved justification for PDF/print export."""
    export = []
    for row in rows:
        if not row.get('justification_approved'):
            continue
        export.append({
            'date': row['date'],
            'am_in': row.get('am_in'),
            'am_out': row.get('am_out'),
            'pm_in': row.get('pm_in'),
            'pm_out': row.get('pm_out'),
            'remarks': row.get('remarks') or '',
            'justification_reason': row.get('justification_reason') or '',
            'accomplishment_text': row.get('accomplishment_text') or '',
        })
    return export


def has_approved_justification_for_export(rows: list[dict]) -> bool:
    return any(r.get('justification_approved') for r in rows)
