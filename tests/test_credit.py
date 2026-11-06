from datetime import date

from arena.db.client import MemoryDB
from arena.llm.credit import credit_status

CFG = {"credit_usd": 10.0, "credit_added_on": "2026-11-06", "low_credit_warning_usd": 2.0}


def test_countdown_from_logged_costs_and_estimated_end_date():
    db = MemoryDB()
    db.insert("llm_usage", [{"cost_usd": 0.5, "created_at": "2026-11-06T23:40:00+00:00"},
                            {"cost_usd": 0.5, "created_at": "2026-11-09T23:40:00+00:00"},
                            {"cost_usd": 9.0, "created_at": "2026-11-01T00:00:00+00:00"}])   # before the top-up
    s = credit_status(db, CFG, date(2026, 11, 9), [])
    assert s["spent_usd"] == 1.0 and s["left_usd"] == 9.0 and not s["low"] and not s["out_of_credit"]
    assert s["per_trading_day_usd"] == 0.5               # 2 trading days (Fri, Mon)
    assert s["runs_out_around"] == "2026-12-04"          # 18 more trading days, Thanksgiving skipped


def test_low_and_out_of_credit():
    db = MemoryDB()
    db.insert("llm_usage", [{"cost_usd": 8.5, "created_at": "2026-11-20T00:00:00+00:00"}])
    assert credit_status(db, CFG, date(2026, 11, 20), [])["low"] is True
    out = credit_status(db, CFG, date(2026, 11, 20), [{"error": "BadRequestError: Your credit balance is too low to access"}])
    assert out["out_of_credit"] and out["left_usd"] == 0.0
