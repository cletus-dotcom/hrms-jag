-- Flexi-time reference shifts (optional standalone DDL)
CREATE TABLE IF NOT EXISTS flexi_time_schedule (
    id SERIAL PRIMARY KEY,
    shift_code VARCHAR(64) NOT NULL,
    time_in VARCHAR(64) NOT NULL,
    time_out VARCHAR(64) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_flexi_time_schedule_shift_code UNIQUE (shift_code)
);

CREATE INDEX IF NOT EXISTS ix_flexi_time_schedule_sort ON flexi_time_schedule (sort_order, shift_code);
