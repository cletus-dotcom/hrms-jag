-- Quincena extended-service (overtime) authorization for JO/COS employees
CREATE TABLE IF NOT EXISTS jo_cos_extend_service (
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

CREATE INDEX IF NOT EXISTS ix_jo_cos_extend_service_ymq ON jo_cos_extend_service (year, month, quincena_half);
CREATE INDEX IF NOT EXISTS ix_jo_cos_extend_service_emp_ymq ON jo_cos_extend_service (employee_id, year, month, quincena_half);
