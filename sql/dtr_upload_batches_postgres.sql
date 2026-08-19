-- DTR upload batch audit + undo support
CREATE TABLE IF NOT EXISTS dtr_upload_batches (
    id SERIAL PRIMARY KEY,
    uploaded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_by_username VARCHAR(80) NOT NULL DEFAULT '',
    quincena VARCHAR(20) NOT NULL,
    filename VARCHAR(255) NOT NULL DEFAULT '',
    record_count INTEGER NOT NULL DEFAULT 0,
    saved_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    created_count INTEGER NOT NULL DEFAULT 0,
    merged_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    uploaded_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    undone_at TIMESTAMP WITHOUT TIME ZONE,
    undone_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS dtr_upload_batch_items (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES dtr_upload_batches(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    record_date DATE NOT NULL,
    action VARCHAR(20) NOT NULL,
    dtr_record_id INTEGER REFERENCES daily_time_record(id) ON DELETE SET NULL,
    before_am_in TIME,
    before_am_out TIME,
    before_pm_in TIME,
    before_pm_out TIME,
    before_remarks VARCHAR(100),
    before_undertime_hrs SMALLINT,
    before_undertime_mins SMALLINT
);

CREATE INDEX IF NOT EXISTS ix_dtr_upload_batches_uploaded_at ON dtr_upload_batches (uploaded_at DESC);
CREATE INDEX IF NOT EXISTS ix_dtr_upload_batch_items_batch_id ON dtr_upload_batch_items (batch_id);
