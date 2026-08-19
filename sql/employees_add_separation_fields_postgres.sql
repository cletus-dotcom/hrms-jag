-- Add employment separation fields (HRMS)
-- Run on PostgreSQL after pulling latest code.
--
-- This project uses SQLAlchemy create_all() in places, which does NOT automatically
-- ALTER existing tables. Apply this once per database.

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS separation_date date,
    ADD COLUMN IF NOT EXISTS separation_type varchar(60);

-- Legacy status conversion (also run automatically on app startup, or manually via):
--   python migrate_employee_status_values.py
UPDATE employees SET status = 'inactive' WHERE LOWER(TRIM(status)) = 'on_leave';
UPDATE employees SET status = 'separated' WHERE LOWER(TRIM(status)) = 'terminated';

