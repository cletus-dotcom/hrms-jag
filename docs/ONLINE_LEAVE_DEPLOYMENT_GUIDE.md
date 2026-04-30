# Online Leave Application: Design, Schema & Deployment Guide

This guide covers the **online leave application** design, **redesigned PostgreSQL schema** for leave, and **step-by-step deployment** using **Render** (app) and **Supabase** (PostgreSQL), with **sync between Supabase (production) and local server (development)**.

---

## Part 1: Leave Application Process Design

### 1.1 User roles and actions

| Role        | Actions |
|------------|---------|
| **Employee** | Apply for leave, view own leave history, cancel pending request |
| **DTR Uploader** | Same as Employee (if they have leave menu) |
| **HR / Manager / Admin** | View all leave requests, approve/reject, view leave balances (optional) |

### 1.2 Leave application workflow

```
Employee submits leave request (type, start date, end date, reason)
    → status = 'pending'
    → (Optional: check leave balance and deduct when approved)
Approver (HR/Manager/Admin) reviews
    → Approve → status = 'approved', approved_by, approved_at
    → Reject  → status = 'rejected', optional rejection_reason
Employee sees status in "My Leave" / dashboard
(Optional) On approve: deduct days from leave balance; on cancel/reject: no deduction
```

### 1.3 Leave types (examples)

- Vacation Leave (VL)
- Sick Leave (SL)
- Personal Leave / Leave without pay (LWOP)
- Maternity / Paternity
- Others (configurable via `leave_types` table)

---

## Part 2: Redesigned PostgreSQL Schema for Leave

Use the **same schema** on both **Supabase** (production) and **local PostgreSQL** (development) so that:

- The app uses a single `DATABASE_URL` per environment.
- You can run the same migrations on both databases.
- No app code changes when switching between local and Supabase.

### 2.1 Core tables

#### **leave_types** (reference data; optional but recommended)

| Column       | Type         | Description |
|-------------|--------------|-------------|
| id          | SERIAL PK    | |
| code        | VARCHAR(20)  | e.g. VL, SL, LWOP |
| name        | VARCHAR(100) | e.g. Vacation Leave |
| description | TEXT         | Optional |
| is_active   | BOOLEAN      | Default true |
| created_at  | TIMESTAMP    | Default now() |

#### **leave_requests** (redesigned – same structure locally and on Supabase)

| Column          | Type           | Description |
|-----------------|----------------|-------------|
| id              | SERIAL PK      | |
| employee_id     | INT NOT NULL FK(employees.id) | Applicant |
| leave_type      | VARCHAR(50)    | Leave type code or name (e.g. VL, Sick Leave) |
| start_date      | DATE NOT NULL  | |
| end_date        | DATE NOT NULL  | |
| total_days      | DECIMAL(5,2)   | Computed or entered (for reporting) |
| reason          | TEXT           | Optional reason/remarks |
| status          | VARCHAR(20)    | pending, approved, rejected, cancelled. Default 'pending' |
| approved_by     | INT NULL FK(users.id) | Who approved/rejected |
| approved_at     | TIMESTAMP      | When decided |
| rejection_reason| TEXT           | Optional; filled when status = 'rejected' |
| created_at      | TIMESTAMP      | Default now() |
| updated_at      | TIMESTAMP      | Default now(), on update now() |

#### **leave_balances** (optional – for deducting leave when approved)

| Column        | Type          | Description |
|---------------|---------------|-------------|
| id            | SERIAL PK     | |
| employee_id   | INT NOT NULL FK(employees.id) | |
| leave_type    | VARCHAR(50)   | Same as leave_requests.leave_type |
| year          | INT NOT NULL  | e.g. 2026 |
| balance       | DECIMAL(5,2)  | Remaining days (e.g. 15.00) |
| created_at    | TIMESTAMP     | |
| updated_at    | TIMESTAMP     | |

Unique constraint: `(employee_id, leave_type, year)`.

### 2.2 Migration script

Use **`migrate_leave_schema.py`** in the project root to create/update these tables. Run it against both local and Supabase so schemas stay in sync (see Part 4).

---

## Part 3: Syncing Supabase and Local Database

### 3.1 Auto vs manual sync (summary)

| What | Auto-sync? | How |
|------|------------|-----|
| **Schema** (tables, columns) | **Manual** | Run the same migration script on **both** databases when you change the schema. |
| **Data** (rows) | **No sync** | Supabase = production data. Local = development data. They stay separate. |
| **Data copy** (prod → local for testing) | **Manual** | Use `pg_dump` / `pg_restore` or Supabase backup/export when you need a copy. |

There is **no automatic sync** between Supabase and local in this setup. You keep them in sync by:

- **Schema:** Running `migrate_leave_schema.py` (or your migration script) once against local and once against Supabase whenever you add or change tables/columns.
- **Data:** Leaving production data only in Supabase; local is for development. Any one-off copy from prod to local is done manually (e.g. export from Supabase, import into local).

### 3.2 Single source of truth: schema

- **Schema** (tables, columns, constraints) is defined in **migration scripts** in the repo.
- Run the **same migrations** on:
  - **Local PostgreSQL** (development)
  - **Supabase** (production), using the Supabase connection string.

### 3.3 Environment-based database URL

- **Local:** `DATABASE_URL=postgresql://user:pass@localhost:5432/hrms`
- **Render (production):** `DATABASE_URL` = Supabase connection string (from Supabase dashboard).

No code change when switching: the app reads `DATABASE_URL` from the environment.

### 3.4 When to run manual schema sync

- **First-time setup:** Run the migration on **local** and on **Supabase** (Step 2 in Part 4).
- **After you add or change tables/columns:** Edit or add a migration script, run it **locally** to test, then run the **same** script against **Supabase** (via `DATABASE_URL` or Supabase SQL Editor).

### 3.5 Optional: run migrations on Render deploy (Supabase only)

You can run migrations **only for production** (Supabase) automatically on each deploy:

- In Render, set **Build command** to something like:  
  `pip install -r requirements.txt && python migrate_leave_schema.py`  
  (so migrations run during build, using Render’s env `DATABASE_URL` = Supabase).
- **Local** still has **no** auto-sync: you must run the same migration yourself when you change the schema.

So: Supabase schema can be updated automatically on deploy; local remains manual.

### 3.6 Render note

Render may set `DATABASE_URL` automatically if you attach a PostgreSQL instance. For **Supabase**, you **ignore** that and set `DATABASE_URL` yourself to the **Supabase connection string** so the app uses Supabase as the single production DB.

---

## Part 4: Step-by-Step Guide

### Step 1: Supabase setup (PostgreSQL for production)

1. Go to [supabase.com](https://supabase.com) and sign in / sign up.
2. **New project:** Create a project (e.g. `hrms-prod`), choose region, set a strong DB password.
3. **Connection string:** In Supabase: **Project Settings → Database**.
   - Copy **Connection string → URI**.
   - Format: `postgresql://postgres.[project-ref]:[YOUR-PASSWORD]@aws-0-[region].pooler.supabase.com:6543/postgres`
   - For direct (non-pooler) use: **Session mode** URI, port **5432**.
4. **Optional (recommended):** Create a dedicated DB user (e.g. `hrms_app`) with limited privileges and use its connection string in production.
5. Keep this URI secret; you will set it as `DATABASE_URL` on Render (no need to commit it).

### Step 2: Run migrations on Supabase (first-time schema)

Option A – run the app’s migration script against Supabase:

```bash
# From your repo root (e.g. c:\python\hrms)
set DATABASE_URL=postgresql://postgres.[ref]:[PASSWORD]@aws-0-[region].pooler.supabase.com:6543/postgres
python migrate_leave_schema.py
```

Option B – run the SQL (from the migration script) in **Supabase SQL Editor** (paste and run).

Repeat the same migration on your **local** DB so schemas match:

```bash
set DATABASE_URL=postgresql://postgres:password@localhost:5432/hrms
python migrate_leave_schema.py
```

### Step 3: Render setup (deploy the Flask app)

1. Go to [render.com](https://render.com) and sign in (e.g. GitHub).
2. **New → Web Service** and connect your HRMS repository.
3. **Build & deploy:**
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn -w 2 -b 0.0.0.0:$PORT wsgi:app`  
     (uses `wsgi.py` in the repo root; ensure `PORT` is provided by Render).
   - **Root directory:** leave blank if the repo root contains `app`, `requirements.txt`, and `wsgi.py`; otherwise set the subdirectory where the app lives.
4. **Environment:**
   - Add **Environment Variables**:
     - `DATABASE_URL` = your **Supabase** connection string (from Step 1).
     - `SECRET_KEY` = a long random string (e.g. 32+ chars).
   - Do **not** add a Render-managed PostgreSQL instance if you are using Supabase.
5. **Optional:** Add a **runtime.txt** in the repo root with e.g. `python-3.11.0` if you need a specific Python version on Render.
6. Deploy; the app will use Supabase for all data (including leave).

### Step 4: Local development (keep using local PostgreSQL)

1. **Local PostgreSQL:** Ensure the `hrms` database exists and the app can connect (e.g. `postgresql://postgres:password@localhost:5432/hrms`).
2. **Environment:** Use a `.env` file (or shell) with:
   - `DATABASE_URL=postgresql://postgres:password@localhost:5432/hrms`
   - `SECRET_KEY=dev-secret`
3. Run the **same** `migrate_leave_schema.py` (or SQL) on local so **leave_requests** (and optional **leave_types**, **leave_balances**) match Supabase.
4. Start the app locally (e.g. `python run.py` or `flask run`). Leave features will work against the local DB.

### Step 5: Keeping schema in sync (ongoing)

- When you add or change leave-related tables/columns:
  1. Add a new migration script (or append to the existing one) that uses `ALTER TABLE` / `CREATE TABLE` as needed.
  2. Run it **locally** first and test.
  3. Run the **same** script against **Supabase** (via `DATABASE_URL` or SQL Editor).
- This keeps both databases structurally identical for the leave system.

---

## Part 5: Config and security checklist

- **Production (Render):**
  - `DATABASE_URL` = Supabase URI only; never commit it.
  - `SECRET_KEY` = strong random value.
  - Prefer **HTTPS** (Render provides it); set `SESSION_COOKIE_SECURE = True` if you use cookies over HTTPS.
- **Local:**
  - `.env` in `.gitignore`; use `python-dotenv` in `run.py` or in config to load `DATABASE_URL` and `SECRET_KEY` for development.

---

## Part 6: Summary

| Item | Purpose |
|------|--------|
| **Leave process** | Employee applies → Approver approves/rejects; optional balance deduction. |
| **Schema** | Same `leave_requests` (and optional `leave_types`, `leave_balances`) on local and Supabase. |
| **Sync** | Same migration scripts run on both DBs; `DATABASE_URL` points to Supabase on Render and to local DB locally. |
| **Render** | Hosts the Flask app; env `DATABASE_URL` = Supabase. |
| **Supabase** | Production PostgreSQL; run migrations and use its URI in production only. |

After this, implement the **leave application UI** (apply form, list “My Leave”, approver list/approve/reject) and point the app at the same `LeaveRequest` (and optional balance) model so it works on both local and Supabase without schema drift.
