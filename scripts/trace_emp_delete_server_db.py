"""Trace employee-delete blockers on LAN Postgres (no password printed)."""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

import psycopg2

uri = os.environ.get('DATABASE_URL') or 'postgresql://postgres:password@192.168.20.20:5432/hrms'
parsed = urlparse(uri)
print(
    'target',
    f'{parsed.hostname}:{parsed.port}{(parsed.path or "")}',
    'user=',
    parsed.username,
)

emp_badge = (sys.argv[1] if len(sys.argv) > 1 else '16735').strip()

conn = psycopg2.connect(uri)
conn.autocommit = True
cur = conn.cursor()

cur.execute(
    'SELECT id, employee_id, first_name, last_name, status '
    'FROM employees WHERE employee_id = %s',
    (emp_badge,),
)
emp = cur.fetchone()
print('employee', emp)
if not emp:
    conn.close()
    raise SystemExit('employee not found')

eid = emp[0]

cur.execute(
    """
    SELECT tc.table_name, kcu.column_name, rc.delete_rule, c.is_nullable
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    JOIN information_schema.referential_constraints rc
      ON tc.constraint_name = rc.constraint_name
     AND tc.table_schema = rc.constraint_schema
    JOIN information_schema.constraint_column_usage ccu
      ON rc.unique_constraint_name = ccu.constraint_name
     AND rc.constraint_schema = ccu.constraint_schema
    JOIN information_schema.columns c
      ON c.table_schema = kcu.table_schema
     AND c.table_name = kcu.table_name
     AND c.column_name = kcu.column_name
    WHERE tc.constraint_type = 'FOREIGN KEY'
      AND ccu.table_name = 'employees'
    ORDER BY 1, 2
    """
)
fks = cur.fetchall()
print('--- FKs referencing employees ---')
for row in fks:
    print(row)

print('--- non-zero dependent counts ---')
for table, col, rule, nullable in fks:
    try:
        cur.execute(f'SELECT count(*) FROM "{table}" WHERE "{col}" = %s', (eid,))
        n = cur.fetchone()[0]
        if n:
            print(f'HAS {table}.{col}={n} ON DELETE {rule} nullable={nullable}')
    except Exception as exc:  # noqa: BLE001
        print('ERR', table, type(exc).__name__, exc)

print('--- dtr_upload_batch_items for employee ---')
cur.execute(
    """
    SELECT id, batch_id, employee_id, record_date, action, dtr_record_id
    FROM dtr_upload_batch_items
    WHERE employee_id = %s
    ORDER BY id
    LIMIT 20
    """,
    (eid,),
)
rows = cur.fetchall()
print('count_shown', len(rows))
for row in rows:
    print(row)

cur.execute(
    """
    SELECT count(*) FROM dtr_upload_batch_items WHERE employee_id = %s
    """,
    (eid,),
)
print('total_batch_items', cur.fetchone()[0])

print('--- dtr_upload_batch_items FK definitions ---')
cur.execute(
    """
    SELECT conname, pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE conrelid = 'dtr_upload_batch_items'::regclass
      AND contype = 'f'
    """
)
for row in cur.fetchall():
    print(row)

print('--- simulate ORM null update (expect fail if NOT NULL) ---')
try:
    cur.execute('BEGIN')
    cur.execute(
        """
        UPDATE dtr_upload_batch_items
           SET employee_id = NULL
         WHERE employee_id = %s
        """,
        (eid,),
    )
    print('UNEXPECTED: null update succeeded')
    cur.execute('ROLLBACK')
except Exception as exc:  # noqa: BLE001
    print('CONFIRMED:', type(exc).__name__, str(exc).splitlines()[0])
    cur.execute('ROLLBACK')

conn.close()
print('done')
