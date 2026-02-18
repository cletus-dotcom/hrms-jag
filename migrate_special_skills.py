"""
Migration Script: Add Special Skills and Hobbies fields to employee_pds table
Run this script to add the new columns for Section VIII. OTHER INFORMATION - Item 31
Supports up to 15 entries
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_special_skills():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Special Skills and Hobbies fields to employee_pds table...")
            
            columns_to_add = []
            
            # Generate all column definitions (15 entries)
            for entry_num in range(1, 16):
                column_name = f"special_skill{entry_num}"
                columns_to_add.append((column_name, 'VARCHAR(500)'))
            
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
                        if added_count % 5 == 0:
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
    success = migrate_special_skills()
    sys.exit(0 if success else 1)
