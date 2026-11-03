from datetime import date

import pytest

from arena.market_calendar import is_trading_day, next_trading_day, nyse_holidays, previous_trading_day


def test_2026_holidays_match_nyse_published_list():
    assert nyse_holidays(2026) == {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
        date(2026, 11, 26), date(2026, 12, 25),
    }


@pytest.mark.parametrize("d,expected", [
    (date(2026, 11, 2), True),     # Monday
    (date(2026, 11, 7), False),    # Saturday
    (date(2026, 11, 8), False),    # Sunday
    (date(2026, 11, 26), False),   # Thanksgiving
    (date(2027, 12, 24), False),   # Christmas on Saturday -> observed Friday
    (date(2021, 12, 31), True),    # New Year on Saturday: NYSE stays open on Dec 31
    (date(2025, 1, 9), False),     # special closure
    (date(2021, 6, 18), True),     # before Juneteenth became a market holiday
])
def test_is_trading_day(d, expected):
    assert is_trading_day(d) is expected


def test_next_and_previous_trading_day_skip_holidays_and_weekends():
    assert next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)
    assert next_trading_day(date(2026, 11, 6)) == date(2026, 11, 9)
    assert previous_trading_day(date(2026, 1, 2)) == date(2025, 12, 31)
