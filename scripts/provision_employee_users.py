"""
Create user accounts for active employees who do not have one yet.

Username and initial password = employees.employee_id (badge number).

Usage (PowerShell):
  python scripts/provision_employee_users.py
  python scripts/provision_employee_users.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app, db
from app.models import Employee, User
from app.user_provision_service import count_active_employees_without_user, provision_users_for_active_employees


def main() -> int:
    parser = argparse.ArgumentParser(description='Provision users for active employees.')
    parser.add_argument('--dry-run', action='store_true', help='Show how many would be created')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        pending = count_active_employees_without_user()
        print(f'Active employees without user accounts: {pending}')
        if args.dry_run or pending == 0:
            return 0
        result = provision_users_for_active_employees()
        print('ok:', result.get('ok'))
        print('created:', result.get('created'))
        print('linked:', result.get('linked'))
        print('skipped:', result.get('skipped'))
        for err in result.get('errors') or []:
            print('error:', err)
        return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
