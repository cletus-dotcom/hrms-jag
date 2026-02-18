"""
Migration script to add mobile_no and residential address fields to Employee table
Run this script to update the database schema.
"""
import sys
from sqlalchemy import text
from app import create_app, db

def migrate_employee_table():
    app = create_app()
    
    with app.app_context():
        try:
            print("Starting migration: Adding mobile_no and residential address fields to Employee table...")
            
            # Check if columns already exist
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('employees')]
            
            # Add mobile_no column if it doesn't exist
            if 'mobile_no' not in columns:
                print("Adding mobile_no column...")
                db.session.execute(text("ALTER TABLE employees ADD COLUMN mobile_no VARCHAR(20)"))
                print("[OK] mobile_no column added")
            else:
                print("[OK] mobile_no column already exists")
            
            # Add residential address columns if they don't exist
            address_fields = [
                ('residential_house_no', 'VARCHAR(100)'),
                ('residential_street', 'VARCHAR(200)'),
                ('residential_subdivision', 'VARCHAR(200)'),
                ('residential_barangay', 'VARCHAR(100)'),
                ('residential_city', 'VARCHAR(100)'),
                ('residential_province', 'VARCHAR(100)'),
                ('residential_zip_code', 'VARCHAR(10)')
            ]
            
            for field_name, field_type in address_fields:
                if field_name not in columns:
                    print(f"Adding {field_name} column...")
                    db.session.execute(text(f"ALTER TABLE employees ADD COLUMN {field_name} {field_type}"))
                    print(f"[OK] {field_name} column added")
                else:
                    print(f"[OK] {field_name} column already exists")
            
            db.session.commit()
            print("\nMigration completed successfully!")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Migration failed: {str(e)}")
            return False

if __name__ == '__main__':
    success = migrate_employee_table()
    sys.exit(0 if success else 1)
