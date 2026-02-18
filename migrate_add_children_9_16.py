"""
Migration Script: Add Children 9-16 fields to employee_pds table
Run this script to add the new columns for children 9 through 16
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_add_children_9_16():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Children 9-16 fields to employee_pds table...")
            
            # List of columns to add (children 9-16)
            columns_to_add = [
                ("child9_name", "VARCHAR(200)"),
                ("child9_date_of_birth", "DATE"),
                ("child10_name", "VARCHAR(200)"),
                ("child10_date_of_birth", "DATE"),
                ("child11_name", "VARCHAR(200)"),
                ("child11_date_of_birth", "DATE"),
                ("child12_name", "VARCHAR(200)"),
                ("child12_date_of_birth", "DATE"),
                ("child13_name", "VARCHAR(200)"),
                ("child13_date_of_birth", "DATE"),
                ("child14_name", "VARCHAR(200)"),
                ("child14_date_of_birth", "DATE"),
                ("child15_name", "VARCHAR(200)"),
                ("child15_date_of_birth", "DATE"),
                ("child16_name", "VARCHAR(200)"),
                ("child16_date_of_birth", "DATE"),
            ]
            
            # Check if columns exist and add them if they don't
            for column_name, column_type in columns_to_add:
                try:
                    # Check if column exists
                    check_query = text(f"""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name='employee_pds' AND column_name='{column_name}'
                    """)
                    result = db.session.execute(check_query).fetchone()
                    
                    if not result:
                        # Column doesn't exist, add it
                        alter_query = text(f"ALTER TABLE employee_pds ADD COLUMN {column_name} {column_type}")
                        db.session.execute(alter_query)
                        print(f"  [OK] Added column: {column_name}")
                    else:
                        print(f"  [SKIP] Column already exists: {column_name}")
                except Exception as e:
                    print(f"  [ERROR] Error adding column {column_name}: {e}")
                    continue
            
            db.session.commit()
            print("\nMigration completed successfully!")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"\n[ERROR] Error during migration: {e}")
            return False

if __name__ == '__main__':
    success = migrate_add_children_9_16()
    sys.exit(0 if success else 1)
