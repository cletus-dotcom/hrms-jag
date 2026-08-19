-- Portal CMS / branding settings
CREATE TABLE IF NOT EXISTS site_settings (
    section_key VARCHAR(80) PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);
