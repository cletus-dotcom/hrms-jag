"""
Migration Script: Add Work Experience fields to employee_pds table
Run this script to add the new columns for Section V. WORK EXPERIENCE
Supports up to 20 entries
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_work_experience():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Work Experience fields to employee_pds table...")
            
            # Fields for each entry
            fields = [
                ('from_date', 'VARCHAR(50)'),
                ('to_date', 'VARCHAR(50)'),
                ('position_title', 'VARCHAR(500)'),
                ('department', 'VARCHAR(500)'),
                ('status_of_appointment', 'VARCHAR(200)'),
                ('govt_service', 'VARCHAR(10)'),
                ('description_of_duties', 'TEXT')
            ]
            
            columns_to_add = []
            
            # Generate all column definitions (20 entries)
            for entry_num in range(1, 21):
                for field_name, field_type in fields:
                    column_name = f"work_exp{entry_num}_{field_name}"
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
                        if added_count % 20 == 0:
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
    success = migrate_work_experience()
    sys.exit(0 if success else 1)
