CREATE TABLE IF NOT EXISTS jo_cos_payslips (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    year INTEGER NOT NULL,
    month SMALLINT NOT NULL,
    quincena_half VARCHAR(1) NOT NULL,
    employee_name VARCHAR(200) NOT NULL,
    employee_no VARCHAR(50),
    designation VARCHAR(120),
    department_name VARCHAR(200),
    days_worked NUMERIC(8, 2) NOT NULL DEFAULT 0,
    rate_per_day NUMERIC(12, 2) NOT NULL DEFAULT 0,
    gross_salary NUMERIC(12, 2) NOT NULL DEFAULT 0,
    late_hours NUMERIC(8, 2) NOT NULL DEFAULT 0,
    late_deduction NUMERIC(12, 2) NOT NULL DEFAULT 0,
    hdmf_deduction NUMERIC(12, 2) NOT NULL DEFAULT 0,
    net_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    generated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    generated_by_user_id INTEGER REFERENCES users(id),
    CONSTRAINT uq_jo_cos_payslip_emp_ymq UNIQUE (employee_id, year, month, quincena_half)
);

CREATE INDEX IF NOT EXISTS ix_jo_cos_payslips_emp ON jo_cos_payslips (employee_id);
CREATE INDEX IF NOT EXISTS ix_jo_cos_payslips_ymq ON jo_cos_payslips (year, month, quincena_half);
