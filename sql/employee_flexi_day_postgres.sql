-- Per-date flexi shift assignments for employees (optional standalone DDL)
CREATE TABLE IF NOT EXISTS employee_flexi_day (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    work_date DATE NOT NULL,
    flexi_time_schedule_id INTEGER NOT NULL REFERENCES flexi_time_schedule(id) ON DELETE CASCADE,
    CONSTRAINT uq_employee_flexi_day_emp_date UNIQUE (employee_id, work_date)
);

CREATE INDEX IF NOT EXISTS ix_employee_flexi_day_emp_date ON employee_flexi_day (employee_id, work_date);
