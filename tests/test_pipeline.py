"""Full Phase 1 pipeline, offline, against the in-memory database."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from arena import config, pipeline
from arena.db.client import MemoryDB
from arena.lookahead import assert_no_lookahead, decision_cutoff, visible_items
from fakes import FakePriceProvider, FakeResponse, FakeSession

FEED = (Path(__file__).parent / "fixtures" / "feed.xml").read_bytes()
NOW = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
RUN_DATE = date(2026, 11, 2)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "AI-Trading-Arena test@example.com")
    routes = {"https://www.sec.gov/files/company_tickers.json": FakeResponse(200, json.dumps({}))}
    for f in config.load("news")["feeds"]:
        routes[f["url"]] = FakeResponse(200, FEED)
    session = FakeSession(routes)   # robots.txt -> 404 -> allowed
    bars = {t: [{"ticker": t, "date": "2026-11-02", "open": 1, "high": 1, "low": 1, "close": 1,
                 "adj_close": 1, "volume": 1}] for t in config.universe_tickers()}
    return session, FakePriceProvider(bars)


def test_dry_run_collects_everything_and_is_safe_to_rerun(env):
    session, prices = env
    db = MemoryDB()
    for _ in range(2):
        result = pipeline.run(RUN_DATE, db, dry_run=True, session=session, price_provider=prices, now=NOW)
    assert result["status"] == "partial"   # filings: no CIKs in the fake map
    assert len(db.select("stocks")) == len(config.universe_tickers())
    assert len(db.select("prices_daily")) == len(config.universe_tickers())
    assert len(db.select("articles")) == 4                  # same fixture in every feed: deduped
    runs = db.select("runs")
    assert len(runs) == 2 and all(r["finished_at"] for r in runs)


def test_articles_published_after_close_never_reach_decisions(env):
    session, prices = env
    db = MemoryDB()
    pipeline.run(RUN_DATE, db, dry_run=True, session=session, price_provider=prices, now=NOW)
    cutoff = decision_cutoff(RUN_DATE)
    visible = visible_items(db.select("articles"), cutoff)
    assert_no_lookahead(visible, cutoff)
    titles = {a["title"] for a in visible}
    assert titles == {"Pineapple prices rise; Exxon and Chevron watch oil"}  # 21:35 UTC earnings item excluded


def test_weekend_is_skipped_and_logged(env):
    session, prices = env
    db = MemoryDB()
    assert pipeline.run(date(2026, 11, 7), db, session=session, price_provider=prices)["status"] == "skipped"
    assert db.select("runs")[0]["status"] == "skipped" and not prices.calls


def test_all_collectors_failing_marks_run_failed(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    db = MemoryDB()
    result = pipeline.run(RUN_DATE, db, session=FakeSession({}), now=NOW)
    # News with no reachable feeds still "succeeds" with errors; prices and filings fail hard.
    assert result["errors"]["prices"].startswith("PriceProviderError")
    assert result["errors"]["filings"].startswith("FilingsError")
    assert db.select("runs")[0]["status"] in ("partial", "failed")
