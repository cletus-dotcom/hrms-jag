# HRMS Quick Start Guide

## Prerequisites Check

1. **Python 3.8+** ✓ (You have Python 3.14.0)
2. **PostgreSQL** - Make sure PostgreSQL is installed and running
3. **Database** - Create a database named `hrms` (or update config)

## Step-by-Step Setup

### 1. Install Dependencies (if not already done)
```powershell
cd C:\python\hrms
pip install -r requirements.txt
```

### 2. Set Up PostgreSQL Database

**Option A: Using psql (PostgreSQL command line)**
```powershell
# Connect to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE hrms;

# Exit psql
\q
```

**Option B: Using pgAdmin or another GUI tool**
- Create a new database named `hrms`

### 3. Update Database Configuration (if needed)

Edit `app/config.py` if your PostgreSQL credentials are different:
```python
SQLALCHEMY_DATABASE_URI = 'postgresql://username:password@localhost/hrms'
```

### 4. Initialize Database

```powershell
python setup_db.py
```

This will:
- Create all database tables
- Create the default admin user

### 5. Run the Application

```powershell
python run.py
```

The server will start on `http://127.0.0.1:8015`

### 6. Login

- **URL**: http://127.0.0.1:8015
- **ID Number**: `ADMIN001`
- **Password**: `admin123`

## Troubleshooting

### Database Connection Error

If you see: `could not connect to server` or `database does not exist`

1. Check PostgreSQL is running:
   ```powershell
   # Check if PostgreSQL service is running
   Get-Service -Name postgresql*
   ```

2. Verify database exists:
   ```powershell
   psql -U postgres -l
   ```

3. Update connection string in `app/config.py`

### Port Already in Use

If port 8015 is already in use, you can change it:
```powershell
$env:PORT=8016
python run.py
```

Or edit `run.py` to change the default port.

### Module Not Found Errors

Make sure all dependencies are installed:
```powershell
pip install -r requirements.txt
```

## Common Commands

**Start server:**
```powershell
python run.py
```

**Setup database:**
```powershell
python setup_db.py
```

**Check if packages are installed:**
```powershell
python -c "import flask; import flask_sqlalchemy; import flask_login; import waitress; print('OK')"
```

## Next Steps

After successful login, you can:
- View the dashboard with statistics
- Manage employees (once you add more features)
- Handle leave requests
- Track attendance

For production deployment, remember to:
- Change the default admin password
- Set a strong SECRET_KEY
- Enable HTTPS
- Update database credentials
