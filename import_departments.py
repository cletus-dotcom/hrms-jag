"""
Import Departments from CSV
Run with: python import_departments.py
"""
import csv
import sys
from datetime import datetime
from app import create_app, db
from app.models import Department

def import_departments(csv_file_path):
    app = create_app()
    
    with app.app_context():
        try:
            imported_count = 0
            skipped_count = 0
            error_count = 0
            
            with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                
                for row in reader:
                    try:
                        name = row['name'].strip()
                        description = row['description'].strip() if row.get('description') else None
                        manager_id = row['manager_id'].strip() if row.get('manager_id') else None
                        
                        # Skip if name is empty
                        if not name:
                            print(f"Skipping row with empty name: {row}")
                            skipped_count += 1
                            continue
                        
                        # Check if department already exists
                        existing = Department.query.filter_by(name=name).first()
                        if existing:
                            print(f"Department '{name}' already exists. Skipping...")
                            skipped_count += 1
                            continue
                        
                        # Create department
                        department = Department(
                            name=name,
                            description=description if description else None,
                            manager_id=int(manager_id) if manager_id and manager_id.isdigit() else None
                        )
                        
                        db.session.add(department)
                        imported_count += 1
                        print(f"Imported: {name}")
                        
                    except Exception as e:
                        print(f"Error importing row {row}: {str(e)}")
                        error_count += 1
                        continue
                
                # Commit all changes
                db.session.commit()
                print(f"\nImport completed!")
                print(f"  - Imported: {imported_count}")
                print(f"  - Skipped: {skipped_count}")
                print(f"  - Errors: {error_count}")
                return True
                
        except FileNotFoundError:
            print(f"Error: CSV file not found at {csv_file_path}")
            return False
        except Exception as e:
            db.session.rollback()
            print(f"Error importing departments: {str(e)}")
            return False

if __name__ == '__main__':
    csv_path = r"C:\Users\jagna\MICT\IT_projects\System\hrmo\departments.csv"
    
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    
    print(f"Importing departments from: {csv_path}")
    print("=" * 50)
    
    success = import_departments(csv_path)
    sys.exit(0 if success else 1)
