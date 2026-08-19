"""Portal administrator console — CMS, branding, data import."""

from __future__ import annotations

import json
import os
import subprocess

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from flask_login import logout_user
from io import BytesIO

from app.portal_auth import is_portal_admin_session, portal_admin_required
from app.portal_import_service import (
    build_template_csv,
    build_template_xlsx,
    commit_import,
    export_table_csv,
    get_model_by_table,
    list_importable_tables,
    preview_import,
    purge_table,
)
from app.portal_backup_service import backup_hrms_db, list_recent_backups
from app.portal_defaults import SERVICE_ICON_OPTIONS
from app.portal_site_service import (
    branding_logo_url,
    branding_seal_url,
    get_branding,
    get_landing_cms,
    site_upload_dir,
    update_branding_from_form,
    update_landing_carousel_from_form,
    update_landing_footer_from_form,
    update_landing_hero_from_form,
    update_landing_mission_from_form,
    update_landing_services_from_form,
    update_landing_splash_from_form,
    update_landing_stats_from_form,
)

portal_admin = Blueprint('portal_admin', __name__, url_prefix='/portal-admin')


def _safe_next(default_endpoint='portal_admin.console'):
    nxt = (request.args.get('next') or request.form.get('next') or '').strip()
    if nxt.startswith('/portal-admin'):
        return nxt
    return url_for(default_endpoint)


@portal_admin.route('/login', methods=['GET', 'POST'])
def login():
    """Legacy URL — portal admins sign in on the main /login page."""
    if is_portal_admin_session():
        return redirect(url_for('portal_admin.console'))
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()
    if not next_url.startswith('/portal-admin'):
        next_url = url_for('portal_admin.console')
    return redirect(url_for('routes.login', next=next_url))


@portal_admin.route('/logout', methods=['GET', 'POST'])
def logout():
    from flask import session

    logout_user()
    session.clear()
    session.modified = True
    flash('Portal administrator signed out.', 'info')
    return redirect(url_for('routes.index'))


@portal_admin.route('/')
@portal_admin_required
def console():
    tables = list_importable_tables()
    branding = get_branding()
    return render_template(
        'portal_admin/console.html',
        tables=tables,
        branding=branding,
        logo_url=branding_logo_url(branding),
        table_count=len(tables),
        recent_backups=list_recent_backups(),
    )


@portal_admin.route('/branding', methods=['GET', 'POST'])
@portal_admin_required
def branding():
    data = get_branding()
    if request.method == 'POST':
        try:
            update_branding_from_form(request.form, request.files)
            flash('Branding updated.', 'success')
            return redirect(url_for('portal_admin.branding'))
        except ValueError as exc:
            flash(str(exc), 'error')
        data = get_branding()
    return render_template(
        'portal_admin/branding.html',
        branding=data,
        logo_url=branding_logo_url(data),
        seal_url=branding_seal_url(data),
    )


@portal_admin.route('/cms/landing', methods=['GET', 'POST'])
@portal_admin_required
def cms_landing():
    cms = get_landing_cms()
    section = (request.args.get('section') or request.form.get('section') or 'hero').strip()
    if request.method == 'POST':
        try:
            if section == 'hero':
                update_landing_hero_from_form(request.form)
            elif section == 'services':
                update_landing_services_from_form(request.form)
            elif section == 'mission':
                update_landing_mission_from_form(request.form)
            elif section == 'stats':
                update_landing_stats_from_form(request.form)
            elif section == 'footer':
                update_landing_footer_from_form(request.form)
            elif section == 'splash':
                update_landing_splash_from_form(request.form)
            elif section == 'carousel':
                update_landing_carousel_from_form(request.form)
            flash('Landing content saved.', 'success')
            return redirect(url_for('portal_admin.cms_landing', section=section))
        except Exception as exc:
            flash(f'Could not save: {exc}', 'error')
        cms = get_landing_cms()
    return render_template(
        'portal_admin/cms_landing.html',
        cms=cms,
        section=section,
        branding=get_branding(),
        logo_url=branding_logo_url(),
        service_icon_options=SERVICE_ICON_OPTIONS,
    )


@portal_admin.route('/import')
@portal_admin_required
def import_home():
    from app.portal_import_service import IMPORT_RESOLVER_VERSION
    return render_template(
        'portal_admin/import.html',
        tables=list_importable_tables(),
        import_resolver_version=IMPORT_RESOLVER_VERSION,
    )


@portal_admin.route('/import/status')
@portal_admin_required
def import_status():
    from app.portal_import_service import IMPORT_RESOLVER_VERSION
    return jsonify({'ok': True, 'import_resolver': IMPORT_RESOLVER_VERSION})


@portal_admin.route('/import/<table_key>/template.csv')
@portal_admin_required
def import_template_csv(table_key):
    model = get_model_by_table(table_key)
    if not model:
        flash('Unknown table.', 'error')
        return redirect(url_for('portal_admin.import_home'))
    payload = build_template_csv(model)
    return send_file(
        BytesIO(payload.encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'{table_key}_import_template.csv',
    )


@portal_admin.route('/import/<table_key>/template.xlsx')
@portal_admin_required
def import_template_xlsx(table_key):
    model = get_model_by_table(table_key)
    if not model:
        flash('Unknown table.', 'error')
        return redirect(url_for('portal_admin.import_home'))
    payload = build_template_xlsx(model)
    return send_file(
        BytesIO(payload),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'{table_key}_import_template.xlsx',
    )


@portal_admin.route('/import/<table_key>/export.csv')
@portal_admin_required
def import_export_csv(table_key):
    model = get_model_by_table(table_key)
    if not model:
        return jsonify({'ok': False, 'error': 'Unknown table.'}), 404
    payload = export_table_csv(model)
    return send_file(
        BytesIO(payload.encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'{table_key}_export.csv',
    )


@portal_admin.route('/import/<table_key>/preview', methods=['POST'])
@portal_admin_required
def import_preview(table_key):
    model = get_model_by_table(table_key)
    if not model:
        return jsonify({'ok': False, 'error': 'Unknown table.'}), 404
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'ok': False, 'error': 'Choose a CSV or Excel file.'}), 400
    try:
        result = preview_import(model, file)
        status = 200 if result.get('ok', True) else 400
        return jsonify(result), status
    except Exception as exc:
        current_app.logger.exception('Import preview failed for %s', table_key)
        return jsonify({'ok': False, 'error': f'Preview failed: {exc}'}), 400


@portal_admin.route('/import/<table_key>/commit', methods=['POST'])
@portal_admin_required
def import_commit(table_key):
    model = get_model_by_table(table_key)
    if not model:
        return jsonify({'ok': False, 'error': 'Unknown table.'}), 404
    payload = request.get_json(silent=True) or {}
    rows = payload.get('rows') or []
    result = commit_import(model, rows)
    status = 200 if result.get('ok') else 400
    return jsonify(result), status


@portal_admin.route('/import/<table_key>/purge', methods=['POST'])
@portal_admin_required
def import_purge(table_key):
    model = get_model_by_table(table_key)
    if not model:
        return jsonify({'ok': False, 'error': 'Unknown table.'}), 404
    payload = request.get_json(silent=True) or {}
    result = purge_table(model, payload.get('confirm_text', ''))
    status = 200 if result.get('ok') else 400
    return jsonify(result), status


@portal_admin.route('/backup', methods=['POST'])
@portal_admin_required
def backup_database():
    try:
        filepath = backup_hrms_db()
        flash(f'Database backup created: {os.path.basename(filepath)}', 'success')
    except FileNotFoundError:
        flash('pg_dump was not found. Install PostgreSQL client tools or add pg_dump to PATH.', 'error')
    except subprocess.CalledProcessError as exc:
        flash(f'Backup failed (pg_dump exit {exc.returncode}). Check database connectivity.', 'error')
    except Exception as exc:
        flash(f'Backup failed: {exc}', 'error')
    return redirect(url_for('portal_admin.console'))


@portal_admin.route('/backup/download/<filename>')
@portal_admin_required
def backup_download(filename):
    import re

    safe = re.sub(r'[^A-Za-z0-9._-]', '', filename or '')
    if not safe.startswith('hrms_') or not (safe.endswith('.backup') or safe.endswith('.sql')):
        flash('Invalid backup file.', 'error')
        return redirect(url_for('portal_admin.console'))
    from app.portal_backup_service import backup_output_dir

    directory = backup_output_dir()
    path = os.path.join(directory, safe)
    if not os.path.isfile(path):
        flash('Backup file not found.', 'error')
        return redirect(url_for('portal_admin.console'))
    return send_file(path, as_attachment=True, download_name=safe)
