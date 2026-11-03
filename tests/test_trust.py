from datetime import date, datetime, timedelta, timezone

import pytest

from arena import config
from arena.db.client import MemoryDB
from arena.trust.scoring import base_date, corroborate, evaluate_claims, horizon_end, judge

CFG = config.load("trust")
NOW = datetime(2026, 11, 20, 23, 30, tzinfo=timezone.utc)


def test_base_date_is_last_close_before_publication():
    assert base_date("2026-11-02T21:30:00+00:00") == date(2026, 11, 2)   # 4:30pm NY Monday, after close
    assert base_date("2026-11-02T15:00:00+00:00") == date(2026, 10, 30)  # 10am NY Monday -> Friday's close
    assert base_date("2026-11-07T15:00:00+00:00") == date(2026, 11, 6)   # Saturday -> Friday
    assert horizon_end(date(2026, 11, 20), 5) == date(2026, 11, 30)      # skips Thanksgiving + weekend


@pytest.mark.parametrize("direction,excess,expected", [
    ("positive", 0.03, "correct"), ("positive", -0.03, "incorrect"), ("positive", 0.005, "unclear"),
    ("negative", -0.03, "correct"), ("negative", 0.03, "incorrect"), ("neutral", 0.10, "unclear"),
    ("positive", None, "unclear"),
])
def test_judge(direction, excess, expected):
    assert judge(direction, excess, 0.01) == expected


def world():
    db = MemoryDB()
    db.insert("sources", [{"id": 1, "name": "Wire", "domain": "wire.com", "trust_alpha": 1, "trust_beta": 1, "trust_score": 0.5, "claims_evaluated": 0},
                          {"id": 2, "name": "Blog", "domain": "blog.com", "trust_alpha": 1, "trust_beta": 1, "trust_score": 0.5, "claims_evaluated": 0},
                          {"id": 3, "name": "SEC EDGAR", "domain": "sec.gov", "trust_alpha": 1, "trust_beta": 1, "trust_score": 0.5, "claims_evaluated": 0}])
    rows, d = [], date(2026, 10, 26)
    aapl, spy = 100.0, 100.0
    while d <= date(2026, 11, 20):
        if d.weekday() < 5:
            rows += [{"ticker": "AAPL", "date": d.isoformat(), "close": aapl, "adj_close": aapl},
                     {"ticker": "SPY", "date": d.isoformat(), "close": spy, "adj_close": spy}]
            aapl *= 1.01      # AAPL beats SPY by ~1%/day
            spy *= 1.001
        d += timedelta(days=1)
    db.insert("prices_daily", rows)
    return db


def card(db, cid, direction, sources, published="2026-11-02T21:30:00+00:00", practice=False, ctype="product", conf=0.8, ticker="AAPL"):
    db.insert("fact_cards", [{"id": cid, "ticker": ticker, "claim": f"claim {cid}", "direction": direction,
                              "claim_type": ctype, "horizon_days": 5, "confidence": conf,
                              "published_at": published, "practice": practice}])
    db.insert("fact_card_sources", [{"fact_card_id": cid, "article_id": cid * 10 + s, "source_id": s} for s in sources])


def test_matured_claims_update_source_trust_once():
    db = world()
    card(db, 1, "positive", [1])          # right
    card(db, 2, "negative", [2])          # wrong
    card(db, 3, "positive", [1], practice=True)   # practice: never scored
    s = evaluate_claims(db, date(2026, 11, 20), CFG, "SPY", NOW)
    assert (s["correct"], s["incorrect"]) == (1, 1)
    src = {r["id"]: r for r in db.select("sources")}
    assert src[1]["trust_score"] == pytest.approx(2 / 3) and src[1]["claims_evaluated"] == 1
    assert src[2]["trust_score"] == pytest.approx(1 / 3)
    assert evaluate_claims(db, date(2026, 11, 20), CFG, "SPY", NOW)["correct"] == 0   # idempotent
    assert {e["fact_card_id"] for e in db.select("claim_evaluations")} == {1, 2}


def test_claims_wait_until_their_horizon_passes():
    db = world()
    card(db, 1, "positive", [1], published="2026-11-18T21:30:00+00:00")
    s = evaluate_claims(db, date(2026, 11, 20), CFG, "SPY", NOW)
    assert s["waiting"] == 1 and db.select("claim_evaluations") == []


def test_official_contradiction_marks_claim_incorrect():
    db = world()
    card(db, 1, "negative", [2], ctype="earnings", published="2026-11-17T15:00:00+00:00")   # rumour: bad earnings
    card(db, 2, "positive", [3], ctype="earnings", published="2026-11-18T21:00:00+00:00")   # 8-K says otherwise
    s = evaluate_claims(db, date(2026, 11, 20), CFG, "SPY", NOW)
    assert s["contradicted"] == 1
    ev = db.select("claim_evaluations", {"fact_card_id": 1})[0]
    assert ev["outcome"] == "incorrect" and ev["excess_return"] is None


def test_corroboration_needs_several_trusted_sources():
    db = world()
    card(db, 1, "positive", [1, 2], published="2026-11-19T21:30:00+00:00")
    card(db, 2, "positive", [1], published="2026-11-19T21:30:00+00:00")
    db.update("sources", {"id": 2}, {"trust_score": 0.2})
    corroborate(db, CFG, NOW)
    assert not db.select("fact_cards", {"id": 1})[0].get("corroborated")    # second source distrusted
    db.update("sources", {"id": 2}, {"trust_score": 0.6})
    corroborate(db, CFG, NOW)
    assert db.select("fact_cards", {"id": 1})[0]["corroborated"] is True
    assert not db.select("fact_cards", {"id": 2})[0].get("corroborated")
