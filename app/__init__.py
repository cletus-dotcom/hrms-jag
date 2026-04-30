from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from app.config import Config

db = SQLAlchemy()
login_manager = LoginManager()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'routes.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    
    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return User.query.get(int(user_id))
    
    from app import routes
    app.register_blueprint(routes.bp)
    
    with app.app_context():
        try:
            from app import models  # register all models so create_all() creates every table
            db.create_all()
            # Ensure leave_ledger_deletion exists (audit log); create_all can miss new tables in some setups
            from app.models import LeaveLedgerDeletion
            LeaveLedgerDeletion.__table__.create(db.engine, checkfirst=True)
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
                    ]:
                        if name not in columns:
                            db.session.execute(text(f"ALTER TABLE employees ADD COLUMN {name} {typ}"))
                            db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Warning: Could not add employee salary columns: {e}")
            # Create default admin user if it doesn't exist
            from app.models import User, Employee
            admin_user = User.query.filter_by(username='admin').first()
            if not admin_user:
                admin = User(
                    username='admin',
                    email='admin@hrms.com',
                    role='admin',
                    employee_id='ADMIN001'
                )
                admin.set_password('admin123')  # Change this in production
                db.session.add(admin)
                db.session.flush()  # Get the user ID
                
                # Create employee record for admin
                admin_employee = Employee(
                    employee_id='ADMIN001',
                    user_id=admin.id,
                    first_name='Admin',
                    last_name='User',
                    position='System Administrator',
                    status='active'
                )
                db.session.add(admin_employee)
                db.session.commit()
        except Exception as e:
            print(f"Warning: Could not initialize database: {e}")
            print("Please run 'python setup_db.py' to set up the database.")
    
    return app
