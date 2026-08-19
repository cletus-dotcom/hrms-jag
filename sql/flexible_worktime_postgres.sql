-- Quincena flexible worktime assignments (reference for DTR regeneration)
CREATE TABLE IF NOT EXISTS flexible_worktime (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    year SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    quincena_half VARCHAR(1) NOT NULL,
    date_start DATE NOT NULL,
    date_end DATE NOT NULL,
    duty_mode VARCHAR(20) NOT NULL DEFAULT 'split',
    time_in TIME,
    break_out TIME,
    break_in TIME,
    time_out TIME,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_flexible_worktime_ymq ON flexible_worktime (year, month, quincena_half);
CREATE INDEX IF NOT EXISTS ix_flexible_worktime_emp_ymq ON flexible_worktime (employee_id, year, month, quincena_half);

-- Incremental upgrades for existing installs
ALTER TABLE flexible_worktime ADD COLUMN IF NOT EXISTS duty_mode VARCHAR(20) NOT NULL DEFAULT 'split';
ALTER TABLE flexible_worktime ALTER COLUMN break_out DROP NOT NULL;
ALTER TABLE flexible_worktime ALTER COLUMN break_in DROP NOT NULL;
-- Rest-day rows omit duty times
ALTER TABLE flexible_worktime ALTER COLUMN time_in DROP NOT NULL;
ALTER TABLE flexible_worktime ALTER COLUMN time_out DROP NOT NULL;
