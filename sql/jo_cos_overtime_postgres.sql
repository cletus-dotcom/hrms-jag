-- JO/COS overtime credits from DTR quincena regeneration
CREATE TABLE IF NOT EXISTS jo_cos_overtime (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    work_date DATE NOT NULL,
    year SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    quincena_half VARCHAR(1) NOT NULL,
    overtime_mins INTEGER NOT NULL DEFAULT 0,
    processed_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT uq_jo_cos_overtime_emp_date UNIQUE (employee_id, work_date)
);

CREATE INDEX IF NOT EXISTS ix_jo_cos_overtime_ymq ON jo_cos_overtime (year, month, quincena_half);
CREATE INDEX IF NOT EXISTS ix_jo_cos_overtime_emp_ymq ON jo_cos_overtime (employee_id, year, month, quincena_half);
