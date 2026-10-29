from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from app.config import Config
from app.request_policy import HRMSRequest
import os
import sys

db = SQLAlchemy()
login_manager = LoginManager()

def create_app(config_class=Config):
    # When packaged with PyInstaller (onefile/onedir), templates and static files
    # are extracted under sys._MEIPASS. Point Flask there so pages render on
    # machines that don't have the source tree.
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_dir = sys._MEIPASS  # type: ignore[attr-defined]
        template_folder = os.path.join(base_dir, 'app', 'templates')
        static_folder = os.path.join(base_dir, 'app', 'static')
        app = Flask(__name__, template_folder=template_folder, static_folder=static_folder, static_url_path='/static')
    else:
        app = Flask(__name__)
    app.config.from_object(config_class)
    app.request_class = HRMSRequest

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'routes.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    
    @login_manager.user_loader
    def load_user(user_id):
        from app.portal_auth import PORTAL_ADMIN_USER_ID, PortalAdminUser
        if user_id == PORTAL_ADMIN_USER_ID:
            return PortalAdminUser()
        from app.models import User
        return User.query.get(int(user_id))
    
    from app import routes
    app.register_blueprint(routes.bp)
    from app.portal_admin_routes import portal_admin
    app.register_blueprint(portal_admin)
    from app.ermt_routes import ermt_bp
    app.register_blueprint(ermt_bp)

    @app.context_processor
    def inject_ermt_globals():
        from app.ermt_helpers import ERMT_RECORD_TYPES, display_ermt_record_type
        return {
            'ermt_record_types': ERMT_RECORD_TYPES,
            'display_ermt_record_type': display_ermt_record_type,
        }
    
    with app.app_context():
        try:
            from app import models  # register all models so create_all() creates every table
            from sqlalchemy import text
            db.create_all()
            # Ensure leave_ledger_deletion exists (audit log); create_all can miss new tables in some setups
            from app.models import LeaveLedgerDeletion, JoCosDesignation, JoCosRate, FlexiTimeSchedule, EmployeeFlexiDay, DtrQuincenaWorktimeSummary, WorkHours, FlexibleWorktime, OvertimeAuthorization, JoCosExtendService, JoCosOvertime, JoCosOvertimeLedger, JoCosOvertimeOffsetRequest, HrmsNotification, PlantillaPayslip, ErmtRecord, EventAttendanceBatch, EventAttendance, WorkweekMonthPlan, WorkweekWeek
            LeaveLedgerDeletion.__table__.create(db.engine, checkfirst=True)
            WorkweekMonthPlan.__table__.create(db.engine, checkfirst=True)
            WorkweekWeek.__table__.create(db.engine, checkfirst=True)
            JoCosDesignation.__table__.create(db.engine, checkfirst=True)
            JoCosRate.__table__.create(db.engine, checkfirst=True)
            FlexiTimeSchedule.__table__.create(db.engine, checkfirst=True)
            EmployeeFlexiDay.__table__.create(db.engine, checkfirst=True)
            DtrQuincenaWorktimeSummary.__table__.create(db.engine, checkfirst=True)
            WorkHours.__table__.create(db.engine, checkfirst=True)
            FlexibleWorktime.__table__.create(db.engine, checkfirst=True)
            OvertimeAuthorization.__table__.create(db.engine, checkfirst=True)
            JoCosExtendService.__table__.create(db.engine, checkfirst=True)
            JoCosOvertime.__table__.create(db.engine, checkfirst=True)
            JoCosOvertimeOffsetRequest.__table__.create(db.engine, checkfirst=True)
            JoCosOvertimeLedger.__table__.create(db.engine, checkfirst=True)
            HrmsNotification.__table__.create(db.engine, checkfirst=True)
            PlantillaPayslip.__table__.create(db.engine, checkfirst=True)
            ErmtRecord.__table__.create(db.engine, checkfirst=True)
            EventAttendanceBatch.__table__.create(db.engine, checkfirst=True)
            EventAttendance.__table__.create(db.engine, checkfirst=True)
            try:
                for stmt in (
                    'ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS barangay VARCHAR(120)',
                    'ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS municipality VARCHAR(120)',
                    'ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS province VARCHAR(120)',
                    'ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS location_text VARCHAR(255)',
                ):
                    db.session.execute(text(stmt))
                db.session.commit()
            except Exception:
                db.session.rollback()
            try:
                db.session.execute(text(
                    'ALTER TABLE ermt_records ALTER COLUMN pdf_link TYPE VARCHAR(1000)'
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()
            from app.models import DtrUploadBatch, DtrUploadBatchItem, DtrUploadPunch, DtrUploadPunchFlag, DtrQuincenaPeriod
            DtrUploadBatch.__table__.create(db.engine, checkfirst=True)
            DtrUploadBatchItem.__table__.create(db.engine, checkfirst=True)
            DtrUploadPunch.__table__.create(db.engine, checkfirst=True)
            DtrUploadPunchFlag.__table__.create(db.engine, checkfirst=True)
            DtrQuincenaPeriod.__table__.create(db.engine, checkfirst=True)
            try:
                from sqlalchemy import inspect as sa_inspect
                inspector = sa_inspect(db.engine)
                if 'dtr_upload_punches' in inspector.get_table_names():
                    punch_cols = {c['name'] for c in inspector.get_columns('dtr_upload_punches')}
                    if 'assigned_slot' not in punch_cols:
                        db.session.execute(text(
                            'ALTER TABLE dtr_upload_punches ADD COLUMN assigned_slot VARCHAR(10)'
                        ))
                        db.session.commit()
                    db.session.execute(text(
                        """
                        UPDATE dtr_upload_punches
                        SET assigned_slot = slot
                        WHERE is_selected IS TRUE
                          AND (assigned_slot IS NULL OR assigned_slot = '')
                        """
                    ))
                    db.session.commit()
            except Exception:
                db.session.rollback()
            from app.models import DtrMonthlyPeriod, DtrMonthlySubmission
            DtrMonthlyPeriod.__table__.create(db.engine, checkfirst=True)
            DtrMonthlySubmission.__table__.create(db.engine, checkfirst=True)
            from app.models import DtrAccomplishmentReport
            DtrAccomplishmentReport.__table__.create(db.engine, checkfirst=True)
            from app.models import SiteSetting
            SiteSetting.__table__.create(db.engine, checkfirst=True)
            from app.portal_site_service import ensure_branding_uploads_normalized, seed_site_content
            seed_site_content()
            try:
                ensure_branding_uploads_normalized()
            except Exception as e:
                print(f'Warning: could not normalize branding logos: {e}')
            # Ensure employees table has agency, lgu_class_level, salary_* columns (add if missing)
            from sqlalchemy import text
            from sqlalchemy import inspect
            try:
                inspector = inspect(db.engine)
                if "employees" in inspector.get_table_names():
                    columns = [c["name"] for c in inspector.get_columns("employees")]
                    for name, typ in [
                        ("agency", "VARCHAR(50)"),
                        ("lgu_class_level", "VARCHAR(20)"),
                        ("salary_tranche", "VARCHAR(20)"),
                        ("salary_grade", "INTEGER"),
                        ("salary_step", "INTEGER"),
                        ("flexible_worktime", "BOOLEAN DEFAULT FALSE"),
                        ("flexible_start_time", "TIME"),
                        ("flexible_end_time", "TIME"),
                        ("separation_date", "DATE"),
                        ("separation_type", "VARCHAR(60)"),
                        ("id_photo_ermt_record_id", "INTEGER"),
                    ]:
                        if name not in columns:
                            db.session.execute(text(f"ALTER TABLE employees ADD COLUMN IF NOT EXISTS {name} {typ}"))
                            db.session.commit()
                    # FK for ID photo → eRMT (best-effort; ignore if already present)
                    try:
                        db.session.execute(text(
                            "DO $$ BEGIN "
                            "ALTER TABLE employees ADD CONSTRAINT fk_employees_id_photo_ermt "
                            "FOREIGN KEY (id_photo_ermt_record_id) REFERENCES ermt_records(id) ON DELETE SET NULL; "
                            "EXCEPTION WHEN duplicate_object THEN NULL; "
                            "WHEN undefined_table THEN NULL; END $$;"
                        ))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    if "jo_cos_designation_id" not in columns:
                        db.session.execute(
                            text(
                                "ALTER TABLE employees ADD COLUMN jo_cos_designation_id INTEGER "
                                "REFERENCES jo_cos_designation(id) ON DELETE SET NULL"
                            )
                        )
                        db.session.commit()
                    # Refresh column list after ALTERs
                    columns = [c["name"] for c in inspector.get_columns("employees")]
                    if "id_photo_ermt_record_id" not in columns:
                        db.session.execute(text(
                            "ALTER TABLE employees ADD COLUMN IF NOT EXISTS id_photo_ermt_record_id INTEGER"
                        ))
                        db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Warning: Could not add employee salary columns: {e}")
            try:
                from app.employee_id_photo_service import ensure_temp_id_photo_link
                msg = ensure_temp_id_photo_link()
                if msg:
                    print(msg)
            except Exception as e:
                db.session.rollback()
                print(f'Warning: could not link temp ID photo: {e}')
            try:
                from app.ermt_nas_service import schedule_ermt_nas_connect, nas_connection_status
                # Never block create_app on UNC / WNet — connect in background.
                print(nas_connection_status())
                schedule_ermt_nas_connect()
            except Exception as e:
                print(f'Warning: NAS connection schedule failed: {e}')
            try:
                from migrate_employee_status_values import run_employee_status_migration
                run_employee_status_migration()
            except Exception as e:
                db.session.rollback()
                print(f"Warning: Could not migrate employee status values: {e}")
            try:
                lwop_inspector = inspect(db.engine)
                for table in ('leave_ledger', 'leave_ledger_deletion'):
                    if table not in lwop_inspector.get_table_names():
                        continue
                    cols = {c['name'] for c in lwop_inspector.get_columns(table)}
                    for name, typ in [
                        ('lwop_days', 'NUMERIC(6,3) DEFAULT 0'),
                        ('lwop_balance', 'NUMERIC(8,3) DEFAULT 0'),
                        ('lwop_amount', 'NUMERIC(12,2) DEFAULT 0'),
                    ]:
                        if name not in cols:
                            db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {typ}'))
                            db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f'Warning: Could not add LWOP ledger columns: {e}')
            try:
                from app.leave_ledger_service import purge_prior_year_appointment_spl_wl_grants
                purge_result = purge_prior_year_appointment_spl_wl_grants(
                    deleted_by_username='system',
                )
                if purge_result.get('deleted'):
                    db.session.commit()
                    print(
                        'Purged prior-year SPL/WL appointment grants: '
                        f"{purge_result['deleted']} row(s), "
                        f"{purge_result['employees_affected']} employee(s) "
                        f"(before {purge_result['cutoff']})"
                    )
                else:
                    db.session.rollback()
            except Exception as e:
                db.session.rollback()
                print(f'Warning: Could not purge prior-year SPL/WL appointment grants: {e}')
            try:
                fw_inspector = inspect(db.engine)
                if 'flexible_worktime' in fw_inspector.get_table_names():
                    fw_cols = {c['name'] for c in fw_inspector.get_columns('flexible_worktime')}
                    if 'duty_mode' not in fw_cols:
                        db.session.execute(
                            text(
                                "ALTER TABLE flexible_worktime "
                                "ADD COLUMN IF NOT EXISTS duty_mode VARCHAR(20) NOT NULL DEFAULT 'split'"
                            )
                        )
                        db.session.commit()
                    # Allow null breaks for straight duty; null in/out for rest days
                    for col in ('break_out', 'break_in', 'time_in', 'time_out'):
                        try:
                            db.session.execute(
                                text(f'ALTER TABLE flexible_worktime ALTER COLUMN {col} DROP NOT NULL')
                            )
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
            except Exception as e:
                db.session.rollback()
                print(f'Warning: Could not upgrade flexible_worktime columns: {e}')
            try:
                hist_inspector = inspect(db.engine)
                if 'employee_appointment_history' in hist_inspector.get_table_names():
                    hist_cols = {c['name'] for c in hist_inspector.get_columns('employee_appointment_history')}
                    for name, typ in [
                        ('change_type', 'VARCHAR(40)'),
                        ('effectivity_date', 'DATE'),
                        ('from_sal_step', 'INTEGER'),
                        ('to_sal_step', 'INTEGER'),
                        ('remarks', 'VARCHAR(255)'),
                    ]:
                        if name not in hist_cols:
                            db.session.execute(
                                text(
                                    f'ALTER TABLE employee_appointment_history '
                                    f'ADD COLUMN IF NOT EXISTS {name} {typ}'
                                )
                            )
                            db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f'Warning: Could not add appointment history SI columns: {e}')
            # Default admin (username=admin / employee ADMIN001) is NOT auto-created
            # on every startup — that re-added deleted records. Use setup_db.py once
            # for intentional bootstrap.
        except Exception as e:
            print(f"Warning: Could not initialize database: {e}")
            print("Please run 'python setup_db.py' to set up the database.")
    
    return app
