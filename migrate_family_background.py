"""
Migration Script: Add Family Background fields to employee_pds table
Run this script to add the new columns for Section II. FAMILY BACKGROUND
"""
import sys
from app import create_app, db
from sqlalchemy import text

def migrate_family_background():
    app = create_app()
    
    with app.app_context():
        try:
            print("Adding Family Background fields to employee_pds table...")
            
            # List of columns to add
            columns_to_add = [
                # Section 22: Spouse's Information
                ("spouse_surname", "VARCHAR(100)"),
                ("spouse_first_name", "VARCHAR(100)"),
                ("spouse_middle_name", "VARCHAR(100)"),
                ("spouse_name_extension", "VARCHAR(10)"),
                ("spouse_occupation", "VARCHAR(200)"),
                ("spouse_employer", "VARCHAR(200)"),
                ("spouse_business_address", "VARCHAR(500)"),
                ("spouse_telephone_no", "VARCHAR(20)"),
                
                # Section 23: Children's Information
                ("child1_name", "VARCHAR(200)"),
                ("child1_date_of_birth", "DATE"),
                ("child2_name", "VARCHAR(200)"),
                ("child2_date_of_birth", "DATE"),
                ("child3_name", "VARCHAR(200)"),
                ("child3_date_of_birth", "DATE"),
                ("child4_name", "VARCHAR(200)"),
                ("child4_date_of_birth", "DATE"),
                ("child5_name", "VARCHAR(200)"),
                ("child5_date_of_birth", "DATE"),
                ("child6_name", "VARCHAR(200)"),
                ("child6_date_of_birth", "DATE"),
                ("child7_name", "VARCHAR(200)"),
                ("child7_date_of_birth", "DATE"),
                ("child8_name", "VARCHAR(200)"),
                ("child8_date_of_birth", "DATE"),
                
                # Section 24: Father's Information
                ("father_surname", "VARCHAR(100)"),
                ("father_first_name", "VARCHAR(100)"),
                ("father_middle_name", "VARCHAR(100)"),
                ("father_name_extension", "VARCHAR(10)"),
                
                # Section 25: Mother's Maiden Name
                ("mother_surname", "VARCHAR(100)"),
                ("mother_first_name", "VARCHAR(100)"),
                ("mother_middle_name", "VARCHAR(100)"),
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
    success = migrate_family_background()
    sys.exit(0 if success else 1)
