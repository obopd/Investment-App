from datetime import datetime, timedelta, timezone

from arena import config
from arena.db.client import MemoryDB
from arena.facts.extract import extract_fact_cards, make_validator, similarity
from arena.llm.budget import BudgetGuard
from arena.llm.client import LLMClient

NOW = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
CFG = config.load("facts")
UNIVERSE = {"AAPL", "MSFT"}


def setup_db():
    db = MemoryDB()
    db.insert("sources", [{"id": 1, "name": "Wire A", "domain": "a.com"}, {"id": 2, "name": "Wire B", "domain": "b.com"}])
    db.insert("articles", [
        {"id": 10, "source_id": 1, "url": "u10", "title": "Apple beats estimates and raises guidance", "tickers": ["AAPL"], "published_at": NOW - timedelta(hours=5)},
        {"id": 11, "source_id": 2, "url": "u11", "title": "Apple beats estimates, raises guidance", "tickers": ["AAPL"], "published_at": NOW - timedelta(hours=4)},
        {"id": 12, "source_id": 1, "url": "u12", "title": "How to bake bread", "tickers": [], "published_at": NOW - timedelta(hours=3)},
        {"id": 13, "source_id": 1, "url": "u13", "title": "Old news", "tickers": ["MSFT"], "published_at": NOW - timedelta(days=10)},
    ])
    return db


def practice_llm(db):
    return LLMClient(db, 1, config.load("models"), BudgetGuard(5, 1), {}, practice=True)


def test_extracts_merges_duplicates_and_records_every_source():
    db = setup_db()
    s = extract_fact_cards(db, practice_llm(db), UNIVERSE, CFG, NOW)
    assert s == {"articles_processed": 2, "cards_new": 1, "cards_merged": 1, "skipped_budget": False}
    card = db.select("fact_cards")[0]
    assert card["direction"] == "positive" and card["published_at"] == (NOW - timedelta(hours=5)).isoformat()
    assert {r["source_id"] for r in db.select("fact_card_sources")} == {1, 2}


def test_articles_are_never_processed_twice_and_old_or_untagged_are_ignored():
    db = setup_db()
    llm = practice_llm(db)
    extract_fact_cards(db, llm, UNIVERSE, CFG, NOW)
    calls = llm.calls
    assert extract_fact_cards(db, llm, UNIVERSE, CFG, NOW)["articles_processed"] == 0
    assert llm.calls == calls
    processed = {a["id"] for a in db.select("articles") if a.get("facts_extracted_at")}
    assert processed == {10, 11}


def test_validator_drops_outside_tickers_and_rejects_unknown_articles():
    v = make_validator({10}, UNIVERSE)
    card = {"article_id": 10, "ticker": "tsla", "claim": "Tesla recalls cars", "direction": "negative",
            "claim_type": "product", "horizon_days": 5, "confidence": 0.7}
    assert v({"cards": [card]}) == []
    macro = {**card, "ticker": None, "claim_type": "macro", "claim": "Fed holds rates"}
    assert len(v({"cards": [macro]})) == 1
    try:
        v({"cards": [{**card, "article_id": 99}]})
        raise AssertionError("should fail")
    except ValueError:
        pass


def test_similarity():
    assert similarity("Apple beats estimates", "Apple beats estimates!") == 1
    assert similarity("Apple beats estimates", "Microsoft misses badly") == 0
