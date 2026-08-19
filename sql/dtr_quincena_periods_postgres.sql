-- DTR quincena period lifecycle (upload → review → process)
CREATE TABLE IF NOT EXISTS dtr_quincena_periods (
    id SERIAL PRIMARY KEY,
    year SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    quincena_half VARCHAR(1) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    review_deadline DATE,
    uploaded_at TIMESTAMP,
    review_opened_at TIMESTAMP,
    review_closed_at TIMESTAMP,
    processed_at TIMESTAMP,
    uploaded_by_user_id INTEGER REFERENCES users(id),
    processed_by_user_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dtr_quincena_period UNIQUE (year, month, quincena_half)
);

CREATE INDEX IF NOT EXISTS ix_dtr_quincena_periods_status ON dtr_quincena_periods (status);
