-- Reference designations for Job Order (JO) and Contract of Service (COS) employees.
-- Used by employees.jo_cos_designation_id and the add/edit employee form (HR/Admin).
--
-- To create the table and FK (if missing), run this in psql or your SQL client.
-- To load rows from Excel, use from the project root:
--   python migrate_jo_cos_designation.py "path\\to\\jo_cos_designation.xlsx"
--   python migrate_jo_cos_designation.py --replace   -- reload from default or env JO_COS_DESIGNATION_XLSX

CREATE TABLE IF NOT EXISTS jo_cos_designation (
    id SERIAL PRIMARY KEY,
    designation VARCHAR(500) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_jo_cos_designation_designation UNIQUE (designation)
);

CREATE INDEX IF NOT EXISTS ix_jo_cos_designation_sort_order ON jo_cos_designation (sort_order, id);

-- FK on employees (safe to run once; skip if column already exists)
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS jo_cos_designation_id INTEGER REFERENCES jo_cos_designation (id) ON DELETE SET NULL;
