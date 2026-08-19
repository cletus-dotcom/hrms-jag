"""Electronic Records Management Tool (eRMT) — mirrored from TESDA RMT module.

PDF files live on a user-specified NAS / network path. The server stores only
the network link (pdf_link), not the file bytes.
"""

from __future__ import annotations

import traceback
from datetime import datetime

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app.ermt_helpers import (
    ERMT_RECORD_TYPES,
    can_delete_ermt,
    can_edit_ermt,
    generate_ermt_record_number,
    get_ermt_nas_id_photos_root,
    get_ermt_nas_root,
    normalize_network_pdf_path,
    parse_ermt_date,
    parse_ermt_record_type_fields,
)
from app.ermt_service import (
    ermt_dashboard_stats,
    search_records_query,
    serialize_ermt_record,
)
from app.models import ErmtRecord
from app.portal_auth import is_portal_admin_session

ermt_bp = Blueprint('ermt', __name__, url_prefix='/ermt')


def _role():
    return (getattr(current_user, 'role', None) or '').strip().lower()


def _display_name():
    if is_portal_admin_session():
        return getattr(current_user, 'username', None) or 'Portal Admin'
    emp = getattr(current_user, 'employee', None)
    if emp:
        return emp.full_name
    return getattr(current_user, 'username', None) or 'User'


def _office_name():
    emp = getattr(current_user, 'employee', None)
    if emp and emp.department:
        return emp.department.name
    return 'HRMDO'


def _dashboard_context():
    role = _role()
    return {
        'fullname': _display_name(),
        'role': role.title() if role else 'User',
        'office': _office_name(),
        'can_edit': can_edit_ermt(role) or is_portal_admin_session(),
        'can_delete': can_delete_ermt(role) or is_portal_admin_session(),
        'ermt_record_types': ERMT_RECORD_TYPES,
        'ermt_nas_root': get_ermt_nas_root(),
        'ermt_nas_id_photos': get_ermt_nas_id_photos_root(),
    }


def _require_edit():
    if can_edit_ermt(_role()) or is_portal_admin_session():
        return None
    flash('Access denied. Only Admin/HR can add or edit eRMT records.', 'error')
    return redirect(url_for('ermt.dashboard'))


def _require_delete():
    if can_delete_ermt(_role()) or is_portal_admin_session():
        return None
    flash('Access denied. Only Admin can delete eRMT records.', 'error')
    return redirect(url_for('ermt.dashboard'))


def _validate_record_form(form):
    title = (form.get('title') or '').strip()
    record_type, record_type_part, type_error = parse_ermt_record_type_fields(
        form.get('record_type'),
        form.get('record_type_part'),
    )
    cabinet_number = (form.get('cabinet_number') or '').strip()
    shelf_number = (form.get('shelf_number') or '').strip()
    keywords = (form.get('keywords') or '').strip()
    remarks = (form.get('remarks') or '').strip()
    pdf_raw = (form.get('pdf_link') or '').strip()

    if not title:
        return None, 'Title is required.'
    if type_error:
        return None, type_error
    if not record_type:
        return None, 'Record type is required.'
    if not cabinet_number:
        return None, 'Cabinet number is required.'
    if not shelf_number:
        return None, 'Shelf number is required.'

    try:
        record_date = parse_ermt_date(form.get('record_date')).date()
    except ValueError as exc:
        return None, str(exc)

    pdf_link, pdf_error = normalize_network_pdf_path(pdf_raw)
    if pdf_error:
        return None, pdf_error

    return {
        'title': title,
        'record_type': record_type,
        'record_type_part': record_type_part,
        'record_date': record_date,
        'cabinet_number': cabinet_number,
        'shelf_number': shelf_number,
        'keywords': keywords or None,
        'remarks': remarks or None,
        'pdf_link': pdf_link,
        # Do not store PDFs on the application server.
        'local_file_path': None,
        'drive_file_id': None,
    }, None


@ermt_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('ermt/dashboard.html', **_dashboard_context())


@ermt_bp.route('/get_stats')
@login_required
def get_stats():
    try:
        return jsonify(ermt_dashboard_stats())
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@ermt_bp.route('/get_records')
@login_required
def get_records():
    try:
        term = request.args.get('q', '')
        records = search_records_query(term).all()
        return jsonify([serialize_ermt_record(r, include_details=True) for r in records])
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@ermt_bp.route('/preview_record_number')
@login_required
def preview_record_number():
    date_str = request.args.get('date')
    try:
        for_date = parse_ermt_date(date_str) if date_str else datetime.utcnow()
        return jsonify({'record_number': generate_ermt_record_number(for_date)})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@ermt_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_record():
    denied = _require_edit()
    if denied:
        return denied

    default_date = datetime.utcnow().strftime('%Y-%m-%d')
    preview_number = generate_ermt_record_number(default_date)

    if request.method == 'POST':
        try:
            payload, error = _validate_record_form(request.form)
            if error:
                flash(error, 'error')
                return redirect(url_for('ermt.add_record'))

            record_number = generate_ermt_record_number(payload['record_date'])
            record = ErmtRecord(
                record_number=record_number,
                encoded_by=_display_name(),
                **payload,
            )
            db.session.add(record)
            db.session.commit()
            flash('Record saved successfully.', 'success')
            return redirect(url_for('ermt.dashboard'))
        except Exception as exc:
            db.session.rollback()
            traceback.print_exc()
            flash(f'Error saving record: {exc}', 'error')
            return redirect(url_for('ermt.add_record'))

    return render_template(
        'ermt/form.html',
        mode='add',
        record=None,
        default_date=default_date,
        preview_number=preview_number,
        **_dashboard_context(),
    )


@ermt_bp.route('/edit/<int:record_id>', methods=['GET', 'POST'])
@login_required
def edit_record(record_id):
    denied = _require_edit()
    if denied:
        return denied

    record = ErmtRecord.query.get_or_404(record_id)

    if request.method == 'POST':
        try:
            payload, error = _validate_record_form(request.form)
            if error:
                flash(error, 'error')
                return redirect(url_for('ermt.edit_record', record_id=record_id))

            for key, value in payload.items():
                setattr(record, key, value)

            db.session.commit()
            flash('Record updated successfully.', 'success')
            return redirect(url_for('ermt.dashboard'))
        except Exception as exc:
            db.session.rollback()
            traceback.print_exc()
            flash(f'Error updating record: {exc}', 'error')
            return redirect(url_for('ermt.edit_record', record_id=record_id))

    return render_template(
        'ermt/form.html',
        mode='edit',
        record=record,
        default_date=None,
        preview_number=record.record_number,
        **_dashboard_context(),
    )


@ermt_bp.route('/delete/<int:record_id>', methods=['POST'])
@login_required
def delete_record(record_id):
    denied = _require_delete()
    if denied:
        return jsonify({'status': 'error', 'msg': 'Access denied.'}), 403

    record = ErmtRecord.query.get_or_404(record_id)
    try:
        # PDF remains on the NAS; only the database link is removed.
        db.session.delete(record)
        db.session.commit()
        return jsonify({'status': 'success', 'msg': 'Record deleted.'})
    except Exception as exc:
        db.session.rollback()
        return jsonify({'status': 'error', 'msg': str(exc)}), 500
