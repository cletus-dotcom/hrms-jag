-- JOFEMCO billing records (monthly file import) and scheduled billing deductions

CREATE TABLE IF NOT EXISTS jofemco_billing_records (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    employee_no VARCHAR(50),
    percov VARCHAR(10),
    loan_q1 NUMERIC(12,2) NOT NULL DEFAULT 0,
    loan_q2 NUMERIC(12,2) NOT NULL DEFAULT 0,
    canteen_q1 NUMERIC(12,2) NOT NULL DEFAULT 0,
    canteen_q2 NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_payable NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_jofemco_billing_record_emp_ym UNIQUE (employee_id, year, month)
);

CREATE TABLE IF NOT EXISTS jofemco_billing_deductions (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    month_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    q1_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    q2_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    q1_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    q2_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_jofemco_billing_deduction_emp_ym UNIQUE (employee_id, year, month)
);

CREATE INDEX IF NOT EXISTS ix_jofemco_billing_records_ym ON jofemco_billing_records(year, month);
CREATE INDEX IF NOT EXISTS ix_jofemco_billing_deductions_ym ON jofemco_billing_deductions(year, month);
