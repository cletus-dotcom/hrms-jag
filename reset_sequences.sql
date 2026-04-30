-- Reset PostgreSQL sequences after MAX ID for HRMS database
-- Run this script in pgAdmin Query Tool after restoring database backup
-- This ensures new records get IDs starting after the current maximum
-- Usage: Connect to 'hrms' database in pgAdmin, open Query Tool, paste this script, and execute

-- Users table
SELECT setval('users_id_seq', COALESCE((SELECT MAX(id) FROM users), 1), true);

-- Employees table
SELECT setval('employees_id_seq', COALESCE((SELECT MAX(id) FROM employees), 1), true);

-- Departments table
SELECT setval('departments_id_seq', COALESCE((SELECT MAX(id) FROM departments), 1), true);

-- Salary Grade table
SELECT setval('salary_grade_id_seq', COALESCE((SELECT MAX(id) FROM salary_grade), 1), true);

-- Positions table
SELECT setval('positions_id_seq', COALESCE((SELECT MAX(id) FROM positions), 1), true);

-- Attendance table
SELECT setval('attendance_id_seq', COALESCE((SELECT MAX(id) FROM attendance), 1), true);

-- Leave Requests table
SELECT setval('leave_requests_id_seq', COALESCE((SELECT MAX(id) FROM leave_requests), 1), true);

-- Employee PDS table
SELECT setval('employee_pds_id_seq', COALESCE((SELECT MAX(id) FROM employee_pds), 1), true);

-- Display confirmation
SELECT 'All sequences have been reset to start after current MAX IDs' AS status;
