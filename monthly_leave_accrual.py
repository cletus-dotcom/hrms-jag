"""
Post monthly leave accrual: VL/SL 1.25 each (TABLE 2 for partial first month if hired after
the 1st). January: Jan 1 row with 3 SPL + 5 WL for those appointed before that date.
December: Dec 31 row lapsing unused SPL/WL. Appointment SPL/WL are created when saving
eligible employees in the UI.

Schedule with Windows Task Scheduler or cron on the 1st of each month (for the previous month)
or on the last day of the month (for the current month).

Examples:
  python monthly_leave_accrual.py
  python monthly_leave_accrual.py --year 2026 --month 3

Requires DATABASE_URL / app config matching the running HRMS instance.
"""

from __future__ import annotations

import argparse
from datetime import date

from app import create_app
from app.leave_ledger_service import accrue_monthly_vl_sl_for_month


def default_target_month(today: date) -> tuple[int, int]:
    """Previous calendar month (typical if job runs on the 1st)."""
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def main() -> None:
    parser = argparse.ArgumentParser(description='Monthly VL/SL leave ledger accrual')
    parser.add_argument('--year', type=int, help='Calendar year (e.g. 2026)')
    parser.add_argument('--month', type=int, help='Calendar month 1-12')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.year is not None and args.month is not None:
            y, m = args.year, args.month
            if not (1 <= m <= 12):
                raise SystemExit('month must be 1-12')
        elif args.year is None and args.month is None:
            y, m = default_target_month(date.today())
        else:
            raise SystemExit('Provide both --year and --month, or neither for default (previous month).')

        result = accrue_monthly_vl_sl_for_month(y, m)
        print(result)


if __name__ == '__main__':
    main()
