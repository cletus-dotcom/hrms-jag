-- Daily worktime history (one row per employee per calendar day).
-- Populated on DTR quincena regeneration.

CREATE TABLE IF NOT EXISTS work_hours (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    work_date DATE NOT NULL,
    year SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    quincena_half VARCHAR(1) NOT NULL,
    gross_work_mins INTEGER NOT NULL DEFAULT 0,
    late_mins INTEGER NOT NULL DEFAULT 0,
    undertime_mins INTEGER NOT NULL DEFAULT 0,
    absence_mins INTEGER NOT NULL DEFAULT 0,
    net_rendered_mins INTEGER NOT NULL DEFAULT 0,
    remarks VARCHAR(100),
    processed_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT uq_work_hours_emp_date UNIQUE (employee_id, work_date)
);

CREATE INDEX IF NOT EXISTS ix_work_hours_ymq ON work_hours (year, month, quincena_half);
CREATE INDEX IF NOT EXISTS ix_work_hours_emp_ymq ON work_hours (employee_id, year, month, quincena_half);
