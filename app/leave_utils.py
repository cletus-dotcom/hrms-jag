"""
Leave computation utilities and reference tables.

Contains:
- Daily leave rates for VL/SL (for new appointees with partial months)
- Tardiness/undertime minutes to day conversion table
- Helper functions for computing leave credits and deductions
"""

from datetime import time, date, timedelta
from decimal import Decimal, ROUND_HALF_UP

# Daily Leave Rates Table (TABLE 2 - VL AND SL DAILY BASIS)
# For new appointees: maps number of working days to earned VL/SL
DAILY_LEAVE_RATES = {
    1: Decimal('0.042'),
    2: Decimal('0.083'),
    3: Decimal('0.125'),
    4: Decimal('0.167'),
    5: Decimal('0.208'),
    6: Decimal('0.250'),
    7: Decimal('0.292'),
    8: Decimal('0.333'),
    9: Decimal('0.375'),
    10: Decimal('0.417'),
    11: Decimal('0.458'),
    12: Decimal('0.500'),
    13: Decimal('0.542'),
    14: Decimal('0.583'),
    15: Decimal('0.625'),
    16: Decimal('0.667'),
    17: Decimal('0.708'),
    18: Decimal('0.750'),
    19: Decimal('0.792'),
    20: Decimal('0.833'),
    21: Decimal('0.875'),
    22: Decimal('0.917'),
    23: Decimal('0.958'),
    24: Decimal('1.000'),
    25: Decimal('1.042'),
    26: Decimal('1.083'),
    27: Decimal('1.125'),
    28: Decimal('1.167'),
    29: Decimal('1.208'),
    30: Decimal('1.250'),
}

# Tardiness/Undertime Conversion Table (TABLE-SHOWING)
# Maps minutes to equivalent day deduction
MINUTES_TO_DAY = {
    1: Decimal('0.002'),
    2: Decimal('0.004'),
    3: Decimal('0.006'),
    4: Decimal('0.008'),
    5: Decimal('0.010'),
    6: Decimal('0.012'),
    7: Decimal('0.015'),
    8: Decimal('0.017'),
    9: Decimal('0.019'),
    10: Decimal('0.021'),
    11: Decimal('0.023'),
    12: Decimal('0.025'),
    13: Decimal('0.027'),
    14: Decimal('0.029'),
    15: Decimal('0.031'),
    16: Decimal('0.033'),
    17: Decimal('0.035'),
    18: Decimal('0.037'),
    19: Decimal('0.040'),
    20: Decimal('0.042'),
    21: Decimal('0.044'),
    22: Decimal('0.046'),
    23: Decimal('0.048'),
    24: Decimal('0.050'),
    25: Decimal('0.052'),
    26: Decimal('0.054'),
    27: Decimal('0.056'),
    28: Decimal('0.058'),
    29: Decimal('0.060'),
    30: Decimal('0.062'),
    31: Decimal('0.065'),
    32: Decimal('0.067'),
    33: Decimal('0.069'),
    34: Decimal('0.071'),
    35: Decimal('0.073'),
    36: Decimal('0.075'),
    37: Decimal('0.077'),
    38: Decimal('0.079'),
    39: Decimal('0.081'),
    40: Decimal('0.083'),
    41: Decimal('0.085'),
    42: Decimal('0.087'),
    43: Decimal('0.090'),
    44: Decimal('0.092'),
    45: Decimal('0.094'),
    46: Decimal('0.096'),
    47: Decimal('0.098'),
    48: Decimal('0.100'),
    49: Decimal('0.102'),
    50: Decimal('0.104'),
    51: Decimal('0.106'),
    52: Decimal('0.108'),
    53: Decimal('0.110'),
    54: Decimal('0.112'),
    55: Decimal('0.115'),
    56: Decimal('0.117'),
    57: Decimal('0.119'),
    58: Decimal('0.121'),
    59: Decimal('0.123'),
    60: Decimal('0.125'),
}

# Standard work schedule times
AM_START = time(8, 0)      # 8:00 AM - Expected check-in
AM_END = time(12, 0)       # 12:00 PM - Expected AM break-out
PM_START = time(13, 0)     # 1:00 PM - Expected break-in
PM_END = time(17, 0)       # 5:00 PM - Expected check-out

# Monthly earned leave (standard)
MONTHLY_VL_EARNED = Decimal('1.25')
MONTHLY_SL_EARNED = Decimal('1.25')
YEARLY_SPL_EARNED = Decimal('3.00')
YEARLY_WL_EARNED = Decimal('5.00')


def count_working_weekdays(start: date, end: date) -> int:
    """Count Mon–Fri days inclusive between start and end (for partial-month VL/SL)."""
    if start > end:
        return 0
    n = 0
    d = start
    one = timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += one
    return n


def get_daily_leave_earned(num_days: int) -> Decimal:
    """
    TABLE 2: leave earned for a partial month from a working-day count (Mon–Fri from
    appointment through month-end). Apply the returned amount separately to VL and to SL
    (each type earns this many days for that partial month).
    
    Args:
        num_days: Number of working days in range (1–30; capped at 30 for the table)
    
    Returns:
        Decimal days earned per leave type (VL and SL each)
    """
    if num_days <= 0:
        return Decimal('0')
    if num_days >= 30:
        return DAILY_LEAVE_RATES[30]
    return DAILY_LEAVE_RATES.get(num_days, Decimal('0'))


def minutes_to_day_equivalent(minutes: int) -> Decimal:
    """
    Convert tardiness/undertime minutes to equivalent day deduction.
    
    Args:
        minutes: Number of minutes late or undertime
    
    Returns:
        Decimal value of day equivalent
    """
    if minutes <= 0:
        return Decimal('0')
    if minutes >= 60:
        # For minutes > 60, calculate proportionally
        full_hours = minutes // 60
        remaining_mins = minutes % 60
        base = Decimal('0.125') * full_hours  # 60 mins = 0.125 day
        if remaining_mins > 0:
            base += MINUTES_TO_DAY.get(remaining_mins, Decimal('0'))
        return base
    return MINUTES_TO_DAY.get(minutes, Decimal('0'))


def compute_tardiness_minutes(actual_time: time, expected_time: time) -> int:
    """
    Compute tardiness in minutes (how late after expected time).
    
    Args:
        actual_time: Actual check-in/break-in time
        expected_time: Expected time (8:00 AM or 1:00 PM)
    
    Returns:
        Minutes late (0 if on time or early)
    """
    if actual_time is None:
        return 0
    
    # Convert to minutes since midnight for comparison
    actual_mins = actual_time.hour * 60 + actual_time.minute
    expected_mins = expected_time.hour * 60 + expected_time.minute
    
    if actual_mins > expected_mins:
        return actual_mins - expected_mins
    return 0


def compute_undertime_minutes(actual_time: time, expected_time: time) -> int:
    """
    Compute undertime in minutes (how early before expected time).
    
    Args:
        actual_time: Actual break-out/check-out time
        expected_time: Expected time (12:00 PM or 5:00 PM)
    
    Returns:
        Minutes early (0 if on time or later)
    """
    if actual_time is None:
        return 0
    
    # Convert to minutes since midnight for comparison
    actual_mins = actual_time.hour * 60 + actual_time.minute
    expected_mins = expected_time.hour * 60 + expected_time.minute
    
    if actual_mins < expected_mins:
        return expected_mins - actual_mins
    return 0


def compute_dtr_deductions(am_in: time, am_out: time, pm_in: time, pm_out: time) -> dict:
    """
    Compute tardiness and undertime deductions from a single DTR record.
    
    Args:
        am_in: Morning check-in time
        am_out: Morning break-out time
        pm_in: Afternoon break-in time
        pm_out: Afternoon check-out time
    
    Returns:
        Dictionary with:
            - tardiness_mins: Total tardiness minutes
            - undertime_mins: Total undertime minutes
            - tardiness_days: Day equivalent deduction for tardiness
            - undertime_days: Day equivalent deduction for undertime
    """
    tardiness_mins = 0
    undertime_mins = 0
    
    # Tardiness: Late check-in (after 8:00 AM) or late break-in (after 1:00 PM)
    if am_in:
        tardiness_mins += compute_tardiness_minutes(am_in, AM_START)
    if pm_in:
        tardiness_mins += compute_tardiness_minutes(pm_in, PM_START)
    
    # Undertime: Early break-out (before 12:00 PM) or early check-out (before 5:00 PM)
    if am_out:
        undertime_mins += compute_undertime_minutes(am_out, AM_END)
    if pm_out:
        undertime_mins += compute_undertime_minutes(pm_out, PM_END)
    
    return {
        'tardiness_mins': tardiness_mins,
        'undertime_mins': undertime_mins,
        'tardiness_days': minutes_to_day_equivalent(tardiness_mins),
        'undertime_days': minutes_to_day_equivalent(undertime_mins),
    }


def compute_first_month_leave(hire_date: date, month_end: date) -> Decimal:
    """
    Compute VL/SL earned for a new appointee in their first partial month.
    
    Args:
        hire_date: Employee's appointment/hire date
        month_end: Last day of the month
    
    Returns:
        Decimal value of VL/SL earned for that partial month
    """
    if hire_date > month_end:
        return Decimal('0')
    
    # Count working days from hire_date to month_end (simplified: all days)
    days_worked = (month_end - hire_date).days + 1
    return get_daily_leave_earned(min(days_worked, 30))


def round_leave(value: Decimal, places: int = 3) -> Decimal:
    """
    Round a leave value to specified decimal places.
    
    Args:
        value: Decimal value to round
        places: Number of decimal places (default 3)
    
    Returns:
        Rounded Decimal value
    """
    quantize_str = '0.' + '0' * places
    return value.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
