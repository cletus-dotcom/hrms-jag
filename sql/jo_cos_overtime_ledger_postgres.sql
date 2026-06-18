-- JO/COS overtime credit ledger (earned + offset)
CREATE TABLE IF NOT EXISTS jo_cos_overtime_ledger (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    entry_type VARCHAR(10) NOT NULL,
    transaction_date DATE NOT NULL,
    year SMALLINT,
    month SMALLINT,
    quincena_half VARCHAR(1),
    hours_earned NUMERIC(8, 2),
    offset_date_start DATE,
    offset_date_end DATE,
    offset_mode VARCHAR(10),
    offset_hours NUMERIC(8, 2),
    balance_hours NUMERIC(8, 2) NOT NULL DEFAULT 0,
    offset_request_id INTEGER,
    regen_tag VARCHAR(80),
    particulars VARCHAR(500),
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_jo_cos_ot_ledger_emp ON jo_cos_overtime_ledger (employee_id);
CREATE INDEX IF NOT EXISTS ix_jo_cos_ot_ledger_ymq ON jo_cos_overtime_ledger (year, month, quincena_half);

-- JO/COS overtime offset applications
CREATE TABLE IF NOT EXISTS jo_cos_overtime_offset_request (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    date_start DATE NOT NULL,
    date_end DATE NOT NULL,
    offset_mode VARCHAR(10) NOT NULL,
    total_hours NUMERIC(8, 2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    reason TEXT,
    submitted_by_user_id INTEGER REFERENCES users(id),
    reviewed_by_user_id INTEGER REFERENCES users(id),
    reviewed_at TIMESTAMP,
    rejection_reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

ALTER TABLE jo_cos_overtime_ledger
    DROP CONSTRAINT IF EXISTS jo_cos_overtime_ledger_offset_request_id_fkey;
ALTER TABLE jo_cos_overtime_ledger
    ADD CONSTRAINT jo_cos_overtime_ledger_offset_request_id_fkey
    FOREIGN KEY (offset_request_id) REFERENCES jo_cos_overtime_offset_request(id);

-- In-app notifications
CREATE TABLE IF NOT EXISTS hrms_notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    link_url VARCHAR(500),
    related_type VARCHAR(50),
    related_id INTEGER,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_hrms_notifications_user_unread ON hrms_notifications (user_id, is_read);
