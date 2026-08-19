-- Event attendance (Flag Raising / Flag Retreat / Meeting) from Excel uploads.
-- Columns match: employeeId, timestamp (epoch ms), lat, long + modal date/category.

CREATE TABLE IF NOT EXISTS event_attendance_batches (
    id SERIAL PRIMARY KEY,
    category VARCHAR(40) NOT NULL,
    event_date DATE NOT NULL,
    filename VARCHAR(255) NOT NULL DEFAULT '',
    row_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    unmatched_count INTEGER NOT NULL DEFAULT 0,
    uploaded_by_user_id INTEGER REFERENCES users(id),
    uploaded_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_attendance (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER REFERENCES event_attendance_batches(id) ON DELETE CASCADE,
    employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    badge_employee_id VARCHAR(50) NOT NULL DEFAULT '',
    category VARCHAR(40) NOT NULL,
    event_date DATE NOT NULL,
    punched_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    latitude NUMERIC(12, 7),
    longitude NUMERIC(12, 7),
    barangay VARCHAR(120),
    municipality VARCHAR(120),
    province VARCHAR(120),
    location_text VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_event_attendance_badge_cat_date_ts
        UNIQUE (badge_employee_id, category, event_date, timestamp_ms)
);

CREATE INDEX IF NOT EXISTS ix_event_attendance_cat_date
    ON event_attendance (category, event_date);
CREATE INDEX IF NOT EXISTS ix_event_attendance_employee_date
    ON event_attendance (employee_id, event_date);
CREATE INDEX IF NOT EXISTS ix_event_attendance_batches_cat_date
    ON event_attendance_batches (category, event_date);

ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS barangay VARCHAR(120);
ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS municipality VARCHAR(120);
ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS province VARCHAR(120);
ALTER TABLE event_attendance ADD COLUMN IF NOT EXISTS location_text VARCHAR(255);

