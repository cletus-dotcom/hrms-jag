-- HDMF (Pag-IBIG) contribution and loan records + scheduled deductions

CREATE TABLE IF NOT EXISTS hdmf_contribution_records (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    employment_scope VARCHAR(16) NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    mid_no VARCHAR(50),
    mp2_account_no VARCHAR(50),
    membership_program VARCHAR(120) NOT NULL,
    percov VARCHAR(10),
    monthly_compensation NUMERIC(12,2) NOT NULL DEFAULT 0,
    er_share NUMERIC(12,2) NOT NULL DEFAULT 0,
    ee_share NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_hdmf_contrib_record_emp_ym_scope_prog
        UNIQUE (employee_id, year, month, employment_scope, membership_program)
);

CREATE TABLE IF NOT EXISTS hdmf_contribution_deductions (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL,
    employment_scope VARCHAR(16) NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    membership_program VARCHAR(120) NOT NULL,
    ps_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    gs_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    mp2_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    month_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    deductible_quincena VARCHAR(1) NOT NULL DEFAULT '2',
    deducted_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_hdmf_contrib_deduction_emp_ym_scope
        UNIQUE (employee_id, year, month, employment_scope)
);

CREATE TABLE IF NOT EXISTS hdmf_loan_records (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    employment_scope VARCHAR(16) NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    mid_no VARCHAR(50),
    mpl NUMERIC(12,2) NOT NULL DEFAULT 0,
    salary NUMERIC(12,2) NOT NULL DEFAULT 0,
    housing NUMERIC(12,2) NOT NULL DEFAULT 0,
    safe NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_hdmf_loan_record_emp_ym_scope UNIQUE (employee_id, year, month, employment_scope)
);

CREATE TABLE IF NOT EXISTS hdmf_loan_deductions (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL,
    employment_scope VARCHAR(16) NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    loan_type VARCHAR(32) NOT NULL,
    month_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    deductible_quincena VARCHAR(1) NOT NULL DEFAULT '2',
    deducted_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_hdmf_loan_deduction_emp_ym_scope_type
        UNIQUE (employee_id, year, month, employment_scope, loan_type)
);

CREATE INDEX IF NOT EXISTS ix_hdmf_contribution_records_ym ON hdmf_contribution_records(year, month, employment_scope);
CREATE INDEX IF NOT EXISTS ix_hdmf_contribution_deductions_ym ON hdmf_contribution_deductions(year, month, employment_scope);
CREATE INDEX IF NOT EXISTS ix_hdmf_loan_records_ym ON hdmf_loan_records(year, month, employment_scope);
CREATE INDEX IF NOT EXISTS ix_hdmf_loan_deductions_ym ON hdmf_loan_deductions(year, month, employment_scope);
