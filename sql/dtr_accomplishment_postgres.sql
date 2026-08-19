CREATE TABLE IF NOT EXISTS dtr_accomplishment_report (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    record_date DATE NOT NULL,
    report_text TEXT NOT NULL DEFAULT '',
    updated_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_dtr_accomplishment_emp_date UNIQUE (employee_id, record_date)
);

CREATE INDEX IF NOT EXISTS ix_dtr_accomplishment_emp_date
    ON dtr_accomplishment_report (employee_id, record_date);
