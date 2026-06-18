-- JO / COS daily rates for payroll (matches designation label + status of appointment).
-- Import: python migrate_jo_cos_rates.py "path\\to\\jo_cos_rates.csv"

CREATE TABLE IF NOT EXISTS jo_cos_rate (
    id SERIAL PRIMARY KEY,
    status_of_appointment VARCHAR(50) NOT NULL,
    designation_label VARCHAR(500) NOT NULL,
    rate_per_day NUMERIC(14, 6) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_jo_cos_rate_status_designation UNIQUE (status_of_appointment, designation_label)
);

CREATE INDEX IF NOT EXISTS ix_jo_cos_rate_status ON jo_cos_rate (status_of_appointment);
CREATE INDEX IF NOT EXISTS ix_jo_cos_rate_sort ON jo_cos_rate (sort_order, id);
