-- Step increment metadata on appointment history
ALTER TABLE employee_appointment_history
    ADD COLUMN IF NOT EXISTS change_type VARCHAR(40),
    ADD COLUMN IF NOT EXISTS effectivity_date DATE,
    ADD COLUMN IF NOT EXISTS from_sal_step INTEGER,
    ADD COLUMN IF NOT EXISTS to_sal_step INTEGER,
    ADD COLUMN IF NOT EXISTS remarks VARCHAR(255);
