-- Electronic Records Management Tool (eRMT)
-- Mirrors TESDA RMT rmt_records schema.

CREATE TABLE IF NOT EXISTS ermt_records (
    id SERIAL PRIMARY KEY,
    record_number VARCHAR(24) NOT NULL UNIQUE,
    title VARCHAR(220) NOT NULL,
    record_type VARCHAR(60),
    record_type_part VARCHAR(120),
    record_date DATE,
    pdf_link VARCHAR(1000),
    local_file_path VARCHAR(300),
    drive_file_id VARCHAR(100),
    cabinet_number VARCHAR(20) NOT NULL,
    shelf_number VARCHAR(20) NOT NULL,
    keywords TEXT,
    remarks TEXT,
    encoded_by VARCHAR(150),
    date_encoded TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    date_updated TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_ermt_records_record_date ON ermt_records (record_date DESC);
CREATE INDEX IF NOT EXISTS ix_ermt_records_cabinet ON ermt_records (cabinet_number);
