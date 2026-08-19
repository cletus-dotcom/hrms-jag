-- LWOP columns on leave ledger (plantilla leave without pay tracking)
ALTER TABLE leave_ledger ADD COLUMN IF NOT EXISTS lwop_days NUMERIC(6,3) DEFAULT 0;
ALTER TABLE leave_ledger ADD COLUMN IF NOT EXISTS lwop_balance NUMERIC(8,3) DEFAULT 0;
ALTER TABLE leave_ledger ADD COLUMN IF NOT EXISTS lwop_amount NUMERIC(12,2) DEFAULT 0;

ALTER TABLE leave_ledger_deletion ADD COLUMN IF NOT EXISTS lwop_days NUMERIC(6,3) DEFAULT 0;
ALTER TABLE leave_ledger_deletion ADD COLUMN IF NOT EXISTS lwop_balance NUMERIC(8,3) DEFAULT 0;
ALTER TABLE leave_ledger_deletion ADD COLUMN IF NOT EXISTS lwop_amount NUMERIC(12,2) DEFAULT 0;
