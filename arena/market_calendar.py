"""NYSE trading calendar, computed from the exchange's holiday rules.

Computed rather than taken from a library so there is no extra dependency and the rules are
testable. Unscheduled closures (national days of mourning, storms) are listed by hand.
"""
from datetime import date, timedelta

# Unscheduled full-day closures. Add new ones here if the exchange announces them.
SPECIAL_CLOSURES = {
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11),   # President Reagan
    date(2007, 1, 2),    # President Ford
    date(2012, 10, 29), date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),   # President G.H.W. Bush
    date(2025, 1, 9),    # President Carter
}


def _easter(year: int) -> date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    # Saturday holidays move to Friday, Sunday holidays to Monday.
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    h = set()
    # New Year's Day: NYSE does not close on Friday Dec 31 when Jan 1 is a Saturday.
    ny = date(year, 1, 1)
    if ny.weekday() == 6:
        h.add(ny + timedelta(days=1))
    elif ny.weekday() != 5:
        h.add(ny)
    if year >= 1998:
        h.add(_nth_weekday(year, 1, 0, 3))   # Martin Luther King Jr. Day
    h.add(_nth_weekday(year, 2, 0, 3))       # Washington's Birthday
    h.add(_easter(year) - timedelta(days=2)) # Good Friday
    h.add(_last_weekday(year, 5, 0))         # Memorial Day
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))  # Juneteenth
    h.add(_observed(date(year, 7, 4)))       # Independence Day
    h.add(_nth_weekday(year, 9, 0, 1))       # Labor Day
    h.add(_nth_weekday(year, 11, 3, 4))      # Thanksgiving
    h.add(_observed(date(year, 12, 25)))     # Christmas
    return h


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year) and d not in SPECIAL_CLOSURES


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
