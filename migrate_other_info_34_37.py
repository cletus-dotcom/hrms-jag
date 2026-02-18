"""
Migration Script: Add Items 34-37 fields to employee_pds table
Run this script to add the new columns for Section VIII. OTHER INFORMATION - Items 34-37
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_other_info_34_37():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Items 34-37 fields to employee_pds table...")
            
            columns_to_add = [
                # Item 34
                ('related_third_degree', 'BOOLEAN DEFAULT FALSE'),
                ('related_fourth_degree', 'BOOLEAN DEFAULT FALSE'),
                ('related_details', 'TEXT'),
                # Item 35
                ('admin_offense_guilty', 'BOOLEAN DEFAULT FALSE'),
                ('admin_offense_details', 'TEXT'),
                ('criminally_charged', 'BOOLEAN DEFAULT FALSE'),
                ('criminal_charge_details', 'TEXT'),
                ('criminal_charge_date_filed', 'VARCHAR(50)'),
                ('criminal_charge_status', 'VARCHAR(500)'),
                # Item 36
                ('convicted_crime', 'BOOLEAN DEFAULT FALSE'),
                ('convicted_crime_details', 'TEXT'),
                # Item 37
                ('separated_from_service', 'BOOLEAN DEFAULT FALSE'),
                ('separated_from_service_details', 'TEXT')
            ]
            
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
    success = migrate_other_info_34_37()
    sys.exit(0 if success else 1)
