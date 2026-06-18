-- Reset all table-backed sequences so the next nextval() is max(column)+1.
-- Use after bulk COPY/INSERT with explicit IDs, restores, or manual fixes.
--
-- Run against your HRMS database (example):
--   psql -U postgres -d hrms -v ON_ERROR_STOP=1 -f sql/reset_sequences_postgres.sql
--
-- If `psql` is not on PATH (common on Windows), from project root:
--   set DATABASE_URL=postgresql://user:pass@host:5432/hrms
--   python reset_sequences.py
--
-- Notes:
-- - Covers SERIAL/BIGSERIAL/SMALLSERIAL and GENERATED {BY DEFAULT|ALWAYS} AS IDENTITY
--   when PostgreSQL reports a sequence via pg_get_serial_sequence().
-- - Skips system schemas and temp schemas (pg_temp_*).
-- - Standalone sequences not attached to a column are not changed.

DO $$
DECLARE
  r RECORD;
  max_id bigint;
  stmt text;
BEGIN
  FOR r IN
    SELECT
      format('%I.%I', ns.nspname, c.relname) AS fq_table,
      a.attname AS colname,
      pg_get_serial_sequence(
        format('%I.%I', ns.nspname, c.relname),
        a.attname
      ) AS seq_name
    FROM pg_class c
    JOIN pg_namespace ns ON ns.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
      AND a.attnum > 0
      AND NOT a.attisdropped
    WHERE c.relkind IN ('r', 'p')
      AND ns.nspname NOT IN ('pg_catalog', 'information_schema')
      AND ns.nspname !~ '^pg_temp_'
      AND pg_get_serial_sequence(
        format('%I.%I', ns.nspname, c.relname),
        a.attname
      ) IS NOT NULL
  LOOP
    stmt := format(
      'SELECT COALESCE(MAX(%I)::bigint, 0) FROM %s',
      r.colname,
      r.fq_table
    );
    EXECUTE stmt INTO max_id;

    -- SERIAL/IDENTITY sequences do not allow setval(_, 0, true). Empty table: start at 1.
    IF max_id = 0 THEN
      EXECUTE format(
        'SELECT setval(%L::regclass, 1, false)',
        r.seq_name
      );
    ELSE
      EXECUTE format(
        'SELECT setval(%L::regclass, %s, true)',
        r.seq_name,
        max_id
      );
    END IF;

    RAISE NOTICE 'setval % to % (from %.% )',
      r.seq_name, max_id, r.fq_table, r.colname;
  END LOOP;
END $$;
