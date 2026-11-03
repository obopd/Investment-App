from datetime import date, datetime, timezone

import pytest

from arena.lookahead import (LookAheadError, assert_no_lookahead, decision_cutoff, visible_items,
                             visible_prices)


def test_cutoff_is_4pm_new_york_across_daylight_saving():
    assert decision_cutoff(date(2026, 7, 1)) == datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)
    assert decision_cutoff(date(2026, 11, 2)) == datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)


def test_only_items_published_before_close_are_visible():
    cutoff = decision_cutoff(date(2026, 11, 2))
    items = [
        {"id": 1, "published_at": "2026-11-02T20:59:59+00:00"},
        {"id": 2, "published_at": "2026-11-02T21:00:00+00:00"},   # exactly at close: not visible
        {"id": 3, "published_at": "2026-11-02T21:30:23Z"},        # after-hours earnings
    ]
    assert [i["id"] for i in visible_items(items, cutoff)] == [1]


def test_guard_fails_when_late_data_reaches_an_agent():
    cutoff = decision_cutoff(date(2026, 11, 2))
    with pytest.raises(LookAheadError):
        assert_no_lookahead([{"published_at": "2026-11-02T21:30:23Z"}], cutoff)
    assert_no_lookahead(visible_items([{"published_at": "2026-11-02T21:30:23Z"}], cutoff), cutoff)


def test_naive_timestamps_are_rejected_not_guessed():
    with pytest.raises(ValueError):
        visible_items([{"published_at": "2026-11-02T10:00:00"}], decision_cutoff(date(2026, 11, 2)))


def test_price_bars_up_to_decision_day_only():
    rows = [{"date": "2026-10-30"}, {"date": "2026-11-02"}, {"date": "2026-11-03"}]
    assert [r["date"] for r in visible_prices(rows, date(2026, 11, 2))] == ["2026-10-30", "2026-11-02"]
