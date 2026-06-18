-- Quincena flexible worktime assignments (reference for DTR regeneration)
CREATE TABLE IF NOT EXISTS flexible_worktime (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    year SMALLINT NOT NULL,
    month SMALLINT NOT NULL,
    quincena_half VARCHAR(1) NOT NULL,
    date_start DATE NOT NULL,
    date_end DATE NOT NULL,
    time_in TIME NOT NULL,
    break_out TIME NOT NULL,
    break_in TIME NOT NULL,
    time_out TIME NOT NULL,
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_flexible_worktime_ymq ON flexible_worktime (year, month, quincena_half);
CREATE INDEX IF NOT EXISTS ix_flexible_worktime_emp_ymq ON flexible_worktime (employee_id, year, month, quincena_half);
