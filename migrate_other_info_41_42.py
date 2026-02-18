"""
Migration Script: Add Items 41-42 fields to employee_pds table
Run this script to add the new columns for Section VIII. OTHER INFORMATION - Items 41-42
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_other_info_41_42():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Items 41-42 fields to employee_pds table...")
            
            columns_to_add = [
                # Item 41: References (3 references)
                ('reference1_name', 'VARCHAR(500)'),
                ('reference1_address', 'TEXT'),
                ('reference1_contact', 'VARCHAR(500)'),
                ('reference2_name', 'VARCHAR(500)'),
                ('reference2_address', 'TEXT'),
                ('reference2_contact', 'VARCHAR(500)'),
                ('reference3_name', 'VARCHAR(500)'),
                ('reference3_address', 'TEXT'),
                ('reference3_contact', 'VARCHAR(500)')
                # Item 42: Declaration (no fields needed)
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
    success = migrate_other_info_41_42()
    sys.exit(0 if success else 1)
