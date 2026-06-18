-- GSIS loan records (monthly file import) and scheduled loan deductions

CREATE TABLE IF NOT EXISTS gsis_loan_records (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    bpno VARCHAR(50),
    ps_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    gs_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    ec_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    consoloan NUMERIC(12,2) NOT NULL DEFAULT 0,
    emrgyln NUMERIC(12,2) NOT NULL DEFAULT 0,
    plreg NUMERIC(12,2) NOT NULL DEFAULT 0,
    gfal NUMERIC(12,2) NOT NULL DEFAULT 0,
    mpl NUMERIC(12,2) NOT NULL DEFAULT 0,
    cpl NUMERIC(12,2) NOT NULL DEFAULT 0,
    mpl_lite NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_gsis_loan_record_emp_ym UNIQUE (employee_id, year, month)
);

CREATE TABLE IF NOT EXISTS gsis_loan_deductions (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    loan_type VARCHAR(32) NOT NULL,
    month_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    q1_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    q2_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    q1_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    q2_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_gsis_loan_deduction_emp_ym_type UNIQUE (employee_id, year, month, loan_type)
);

CREATE INDEX IF NOT EXISTS ix_gsis_loan_records_ym ON gsis_loan_records(year, month);
CREATE INDEX IF NOT EXISTS ix_gsis_loan_deductions_ym ON gsis_loan_deductions(year, month);
