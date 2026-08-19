-- Employee ID photo link to eRMT record (NAS/network image path).
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS id_photo_ermt_record_id INTEGER;

DO $$
BEGIN
    ALTER TABLE employees
        ADD CONSTRAINT fk_employees_id_photo_ermt
        FOREIGN KEY (id_photo_ermt_record_id)
        REFERENCES ermt_records(id)
        ON DELETE SET NULL;
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN undefined_table THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS ix_employees_id_photo_ermt
    ON employees (id_photo_ermt_record_id);
