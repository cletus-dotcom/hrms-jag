SELECT setval('users_id_seq', COALESCE((SELECT MAX(id) FROM users), 1), true);

SELECT setval('employees_id_seq', COALESCE((SELECT MAX(id) FROM employees), 1), true);

SELECT setval('departments_id_seq', COALESCE((SELECT MAX(id) FROM departments), 1), true);

SELECT setval('salary_grade_id_seq', COALESCE((SELECT MAX(id) FROM salary_grade), 1), true);

SELECT setval('positions_id_seq', COALESCE((SELECT MAX(id) FROM positions), 1), true);

SELECT setval('attendance_id_seq', COALESCE((SELECT MAX(id) FROM attendance), 1), true);

SELECT setval('leave_requests_id_seq', COALESCE((SELECT MAX(id) FROM leave_requests), 1), true);

SELECT setval('employee_pds_id_seq', COALESCE((SELECT MAX(id) FROM employee_pds), 1), true);

SELECT setval('employee_appoint_history_id_seq', COALESCE((SELECT MAX(id) FROM employee_appointment_history), 1), true);

SELECT 'All sequences have been reset to start after current MAX IDs' AS status;
