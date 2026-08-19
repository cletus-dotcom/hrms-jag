-- Plantilla monthly DTR submission tracking
CREATE TABLE IF NOT EXISTS dtr_monthly_periods (
    id SERIAL PRIMARY KEY,
    year SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    submission_deadline DATE,
    opened_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dtr_monthly_period UNIQUE (year, month)
);

CREATE TABLE IF NOT EXISTS dtr_monthly_submissions (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    year SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    submitted_at TIMESTAMP,
    submitted_by_user_id INTEGER REFERENCES users(id),
    reviewed_at TIMESTAMP,
    reviewed_by_user_id INTEGER REFERENCES users(id),
    review_notes TEXT,
    printed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dtr_monthly_submission_emp_month UNIQUE (employee_id, year, month)
);

CREATE INDEX IF NOT EXISTS ix_dtr_monthly_submissions_year_month ON dtr_monthly_submissions (year, month);
CREATE INDEX IF NOT EXISTS ix_dtr_monthly_submissions_status ON dtr_monthly_submissions (status);
