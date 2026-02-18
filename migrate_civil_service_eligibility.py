"""
Migration Script: Add Civil Service Eligibility fields to employee_pds table
Run this script to add the new columns for Section IV. CIVIL SERVICE ELIGIBILITY
Supports up to 10 entries
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_civil_service_eligibility():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Civil Service Eligibility fields to employee_pds table...")
            
            # Fields for each entry (eligibility, rating, date, place, license_number, license_valid_until)
            fields = [
                ('eligibility', 'VARCHAR(500)'),
                ('rating', 'VARCHAR(50)'),
                ('date', 'VARCHAR(50)'),
                ('place', 'VARCHAR(500)'),
                ('license_number', 'VARCHAR(100)'),
                ('license_valid_until', 'VARCHAR(50)')
            ]
            
            columns_to_add = []
            
            # Generate all column definitions (10 entries)
            for entry_num in range(1, 11):
                for field_name, field_type in fields:
                    column_name = f"civil_service{entry_num}_{field_name}"
                    columns_to_add.append((column_name, field_type))
            
            # Check if columns exist and add them if they don't
            added_count = 0
            skipped_count = 0
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
                        added_count += 1
                        if added_count % 10 == 0:
                            print(f"  [OK] Added {added_count} columns...")
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
    success = migrate_civil_service_eligibility()
    sys.exit(0 if success else 1)
