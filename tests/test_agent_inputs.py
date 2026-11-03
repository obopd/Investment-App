"""No-look-ahead at the agent level: this test FAILS if any agent input contains data
published after the decision time."""
from datetime import date, datetime, timedelta, timezone

import pytest

from arena.agents.inputs import build_inputs, check_inputs
from arena.db.client import MemoryDB
from arena.lookahead import LookAheadError, decision_cutoff

D = date(2026, 11, 2)
CUT = decision_cutoff(D)


def make_db():
    db = MemoryDB()
    db.insert("portfolios", [{"team_id": 1, "cash": 100_000}])
    db.insert("sources", [{"id": 1, "name": "Wire", "trust_score": 0.5}])
    start = D - timedelta(days=100)
    rows, px = [], 100.0
    for i in range(102):
        d = start + timedelta(days=i)
        px *= 1.001
        rows.append({"ticker": "AAPL", "date": d.isoformat(), "open": px, "close": px, "volume": 1000})
    db.insert("prices_daily", rows)        # includes 2026-11-03, the day AFTER the decision
    db.insert("fact_cards", [
        {"id": 1, "ticker": "AAPL", "claim": "before close", "direction": "positive", "claim_type": "other",
         "horizon_days": 5, "confidence": 0.5, "published_at": (CUT - timedelta(minutes=1)).isoformat()},
        {"id": 2, "ticker": "AAPL", "claim": "after close", "direction": "negative", "claim_type": "earnings",
         "horizon_days": 5, "confidence": 0.9, "published_at": (CUT + timedelta(minutes=30)).isoformat()},
    ])
    db.insert("fact_card_sources", [{"fact_card_id": 1, "article_id": 1, "source_id": 1},
                                    {"fact_card_id": 2, "article_id": 2, "source_id": 1}])
    return db


def test_agent_inputs_never_contain_future_data():
    inputs = build_inputs(make_db(), {"min_source_trust": 0}, 1, D, ["AAPL"])
    assert [c["claim"] for c in inputs["fact_cards"]] == ["before close"]
    assert inputs["prices"]["AAPL"]["as_of"] == D.isoformat()
    for c in inputs["fact_cards"]:
        assert datetime.fromisoformat(c["published_at"]) < CUT


def test_guard_raises_if_late_data_slips_in():
    inputs = build_inputs(make_db(), {"min_source_trust": 0}, 1, D, ["AAPL"])
    inputs["fact_cards"].append({"published_at": (CUT + timedelta(seconds=1)).isoformat()})
    with pytest.raises(LookAheadError):
        check_inputs(inputs)
    inputs["fact_cards"].pop()
    inputs["prices"]["AAPL"]["as_of"] = "2026-11-03"
    with pytest.raises(LookAheadError):
        check_inputs(inputs)


def test_low_trust_sources_are_hidden():
    db = make_db()
    db.update("sources", {"id": 1}, {"trust_score": 0.2})
    assert build_inputs(db, {"min_source_trust": 0.4}, 1, D, ["AAPL"])["fact_cards"] == []


def test_live_teams_never_see_practice_cards():
    db = make_db()
    db.update("fact_cards", {"id": 1}, {"practice": True})
    assert build_inputs(db, {"min_source_trust": 0, "mode": "live"}, 1, D, ["AAPL"])["fact_cards"] == []
    assert len(build_inputs(db, {"min_source_trust": 0, "mode": "practice"}, 1, D, ["AAPL"])["fact_cards"]) == 1
