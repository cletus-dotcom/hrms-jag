"""Philippine regular and special non-working holidays (office calendar).

Regular dates follow RA 9492 / EO 292 as commonly observed. Special non-working
dates follow the usual nationwide set; presidential proclamations can still add
or move a day — HR can unflag or add a local Friday on the workweek calendar.

Islamic movable feasts are omitted from auto-detect (proclamation dates vary);
add those Fridays as local holidays when declared.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import NamedTuple

from dateutil.easter import easter

KIND_REGULAR = 'regular'
KIND_SPECIAL = 'special_non_working'

# First day of Chinese New Year (Gregorian), 2020–2040.
_CHINESE_NEW_YEAR = {
    2020: date(2020, 1, 25),
    2021: date(2021, 2, 12),
    2022: date(2022, 2, 1),
    2023: date(2023, 1, 22),
    2024: date(2024, 2, 10),
    2025: date(2025, 1, 29),
    2026: date(2026, 2, 17),
    2027: date(2027, 2, 6),
    2028: date(2028, 1, 26),
    2029: date(2029, 2, 13),
    2030: date(2030, 2, 3),
    2031: date(2031, 1, 23),
    2032: date(2032, 2, 11),
    2033: date(2033, 1, 31),
    2034: date(2034, 2, 19),
    2035: date(2035, 2, 8),
    2036: date(2036, 1, 28),
    2037: date(2037, 2, 15),
    2038: date(2038, 2, 4),
    2039: date(2039, 1, 24),
    2040: date(2040, 2, 12),
}


class PhHoliday(NamedTuple):
    date: date
    name: str
    kind: str


def iso_week_monday(d: date) -> date:
    """Monday of the ISO week that contains ``d`` (Mon=0)."""
    return d - timedelta(days=d.weekday())


def iso_week_friday(week_start: date) -> date:
    return week_start + timedelta(days=4)


def last_monday_of_august(year: int) -> date:
    d = date(year, 8, 31)
    while d.weekday() != 0:
        d -= timedelta(days=1)
    return d


def philippines_holidays_for_year(year: int) -> list[PhHoliday]:
    """Nationwide holidays whose calendar date falls in ``year``."""
    es = easter(year)
    if not isinstance(es, date):
        es = es.date()
    items: list[PhHoliday] = [
        PhHoliday(date(year, 1, 1), "New Year's Day", KIND_REGULAR),
        PhHoliday(es - timedelta(days=3), 'Maundy Thursday', KIND_REGULAR),
        PhHoliday(es - timedelta(days=2), 'Good Friday', KIND_REGULAR),
        PhHoliday(es - timedelta(days=1), 'Black Saturday', KIND_SPECIAL),
        PhHoliday(date(year, 4, 9), 'Araw ng Kagitingan', KIND_REGULAR),
        PhHoliday(date(year, 5, 1), 'Labor Day', KIND_REGULAR),
        PhHoliday(date(year, 6, 12), 'Independence Day', KIND_REGULAR),
        PhHoliday(date(year, 8, 21), 'Ninoy Aquino Day', KIND_SPECIAL),
        PhHoliday(last_monday_of_august(year), 'National Heroes Day', KIND_REGULAR),
        PhHoliday(date(year, 11, 1), "All Saints' Day", KIND_SPECIAL),
        PhHoliday(date(year, 11, 2), "All Souls' Day", KIND_SPECIAL),
        PhHoliday(date(year, 11, 30), 'Bonifacio Day', KIND_REGULAR),
        PhHoliday(date(year, 12, 8), 'Feast of the Immaculate Conception', KIND_SPECIAL),
        PhHoliday(date(year, 12, 24), 'Christmas Eve', KIND_SPECIAL),
        PhHoliday(date(year, 12, 25), 'Christmas Day', KIND_REGULAR),
        PhHoliday(date(year, 12, 30), 'Rizal Day', KIND_REGULAR),
        PhHoliday(date(year, 12, 31), 'Last Day of the Year', KIND_SPECIAL),
        PhHoliday(date(year, 2, 25), 'EDSA People Power Anniversary', KIND_SPECIAL),
    ]
    cny = _CHINESE_NEW_YEAR.get(year)
    if cny:
        items.append(PhHoliday(cny, 'Chinese New Year', KIND_SPECIAL))
    items.sort(key=lambda h: h.date)
    return items


def friday_holidays_touching_year(year: int) -> list[PhHoliday]:
    """
    Official Friday holidays whose ISO week overlaps calendar ``year``.

    Example: New Year's Day Fri 1 Jan 2027 flags week Mon 28 Dec 2026–Sun 3 Jan 2027,
    so it is included when building both 2026 and 2027.
    """
    seen: set[date] = set()
    out: list[PhHoliday] = []
    for y in (year - 1, year, year + 1):
        if y < 1:
            continue
        for hol in philippines_holidays_for_year(y):
            if hol.date.weekday() != 4:
                continue
            monday = iso_week_monday(hol.date)
            sunday = monday + timedelta(days=6)
            if sunday < date(year, 1, 1) or monday > date(year, 12, 31):
                continue
            if hol.date in seen:
                continue
            seen.add(hol.date)
            out.append(hol)
    out.sort(key=lambda h: h.date)
    return out


def mondays_overlapping_year(year: int) -> list[date]:
    """Every ISO-week Monday that overlaps calendar ``year``."""
    first = iso_week_monday(date(year, 1, 1))
    last = iso_week_monday(date(year, 12, 31))
    mondays: list[date] = []
    d = first
    while d <= last:
        mondays.append(d)
        d += timedelta(days=7)
    return mondays


def mondays_overlapping_month(year: int, month: int) -> list[date]:
    import calendar as cal

    first = date(year, month, 1)
    last = date(year, month, cal.monthrange(year, month)[1])
    d = iso_week_monday(first)
    mondays: list[date] = []
    while d <= last:
        mondays.append(d)
        d += timedelta(days=7)
    return mondays
