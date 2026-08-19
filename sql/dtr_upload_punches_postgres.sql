-- Raw biometric punches retained from DTR .dat uploads + duplicate-slot flags for HR review
CREATE TABLE IF NOT EXISTS dtr_upload_punches (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES dtr_upload_batches(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    badge_employee_id VARCHAR(50) NOT NULL DEFAULT '',
    record_date DATE NOT NULL,
    event_code SMALLINT NOT NULL,
    slot VARCHAR(10) NOT NULL,
    punch_time TIME,
    source_timestamp VARCHAR(40) NOT NULL DEFAULT '',
    line_no INTEGER NOT NULL DEFAULT 0,
    sequence_no INTEGER NOT NULL DEFAULT 0,
    is_selected BOOLEAN NOT NULL DEFAULT FALSE,
    assigned_slot VARCHAR(10)
);

CREATE INDEX IF NOT EXISTS ix_dtr_upload_punches_batch_emp_date
    ON dtr_upload_punches (batch_id, employee_id, record_date);
CREATE INDEX IF NOT EXISTS ix_dtr_upload_punches_emp_date_slot
    ON dtr_upload_punches (employee_id, record_date, slot);

CREATE TABLE IF NOT EXISTS dtr_upload_punch_flags (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES dtr_upload_batches(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    record_date DATE NOT NULL,
    slot VARCHAR(10) NOT NULL,
    punch_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    selected_punch_id INTEGER REFERENCES dtr_upload_punches(id) ON DELETE SET NULL,
    resolved_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    resolved_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT uq_dtr_upload_punch_flag UNIQUE (batch_id, employee_id, record_date, slot)
);

CREATE INDEX IF NOT EXISTS ix_dtr_upload_punch_flags_batch_status
    ON dtr_upload_punch_flags (batch_id, status);
CREATE INDEX IF NOT EXISTS ix_dtr_upload_punch_flags_emp_date
    ON dtr_upload_punch_flags (employee_id, record_date, status);

-- Existing installs: add assigned_slot if missing and backfill last-wins selections
ALTER TABLE dtr_upload_punches
    ADD COLUMN IF NOT EXISTS assigned_slot VARCHAR(10);

UPDATE dtr_upload_punches
SET assigned_slot = slot
WHERE is_selected IS TRUE
  AND (assigned_slot IS NULL OR assigned_slot = '');
