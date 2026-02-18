"""
Database Setup Script
Run this to initialize the database and create the default admin user.
"""
import sys
from app import create_app, db
from app.models import User, Employee, Department

def setup_database():
    app = create_app()
    
    with app.app_context():
        try:
            # Create all tables
            print("Creating database tables...")
            db.create_all()
            print("[OK] Database tables created successfully")
            
            # Check if admin user exists
            admin = User.query.filter_by(username='admin').first()
            if not admin:
                print("Creating default admin user...")
                admin = User(
                    username='admin',
                    email='admin@hrms.com',
                    role='admin',
                    employee_id='ADMIN001'
                )
                admin.set_password('admin123')
                db.session.add(admin)
                
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
                print("[OK] Default admin user created")
                print("  ID Number: ADMIN001")
                print("  Password: admin123")
            else:
                print("[OK] Admin user already exists")
            
            print("\nDatabase setup completed successfully!")
            return True
            
        except Exception as e:
            print(f"\n[ERROR] Error setting up database: {e}")
            print("\nPlease ensure:")
            print("1. PostgreSQL is running")
            print("2. Database 'hrms' exists (or update DATABASE_URL in app/config.py)")
            print("3. Database credentials are correct")
            return False

if __name__ == '__main__':
    success = setup_database()
    sys.exit(0 if success else 1)
