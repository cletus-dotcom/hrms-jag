"""
Migration Script: Add Government Issued ID fields to employee_pds table
Run this script to add the new columns below Item 42, Section VIII. OTHER INFORMATION
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_government_issued_id():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Government Issued ID fields to employee_pds table...")
            
            columns_to_add = [
                ('government_issued_id', 'VARCHAR(500)'),
                ('id_license_passport_no', 'VARCHAR(200)'),
                ('date_place_of_issuance', 'VARCHAR(500)'),
            ]
            
            added_count = 0
            skipped_count = 0
            for column_name, column_type in columns_to_add:
                try:
                    check_query = text(f"""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name='employee_pds' AND column_name='{column_name}'
                    """)
                    result = db.session.execute(check_query).fetchone()
                    
                    if not result:
                        alter_query = text(f"ALTER TABLE employee_pds ADD COLUMN {column_name} {column_type}")
                        db.session.execute(alter_query)
                        added_count += 1
                        print(f"  [OK] Added column {column_name}")
                    else:
                        skipped_count += 1
                except Exception as e:
                    print(f"  [ERROR] Error adding column {column_name}: {e}")
                    continue
            
            db.session.commit()
            print(f"\nMigration completed successfully!")
            print(f"  Added: {added_count} columns")
            print(f"  Skipped (already exist): {skipped_count} columns")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"\n[ERROR] Error during migration: {e}")
            return False

if __name__ == '__main__':
    success = migrate_government_issued_id()
    sys.exit(0 if success else 1)
