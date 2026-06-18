-- GSIS Contributions (Plantilla) - per quincena deductions
-- Personal Share (PS) is stored as full monthly value (9% of basic salary),
-- but deducted_amount stores ONLY half of PS (because deduction is per quincena).
-- Government Share (GS) is stored for display/reference (12% of basic salary).

CREATE TABLE IF NOT EXISTS gsis_contributions (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    deductible_quincena VARCHAR(1) NOT NULL CHECK (deductible_quincena IN ('1','2')),
    basic_salary NUMERIC(12,2) NOT NULL DEFAULT 0,
    ps_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    gs_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    month_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    quincena_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    deducted_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_gsis_contrib_emp_ymq UNIQUE (employee_id, year, month, deductible_quincena)
);

CREATE INDEX IF NOT EXISTS ix_gsis_contrib_year_month ON gsis_contributions(year, month);
CREATE INDEX IF NOT EXISTS ix_gsis_contrib_dept ON gsis_contributions(department_id);
CREATE INDEX IF NOT EXISTS ix_gsis_contrib_emp ON gsis_contributions(employee_id);

