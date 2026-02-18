"""
Migration Script: Add Children 17-20 fields to employee_pds table
Run this script to add the new columns for children 17 through 20
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_add_children_17_20():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Children 17-20 fields to employee_pds table...")
            
            # List of columns to add (children 17-20)
            columns_to_add = [
                ("child17_name", "VARCHAR(200)"),
                ("child17_date_of_birth", "DATE"),
                ("child18_name", "VARCHAR(200)"),
                ("child18_date_of_birth", "DATE"),
                ("child19_name", "VARCHAR(200)"),
                ("child19_date_of_birth", "DATE"),
                ("child20_name", "VARCHAR(200)"),
                ("child20_date_of_birth", "DATE"),
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
    success = migrate_add_children_17_20()
    sys.exit(0 if success else 1)
