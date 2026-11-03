import random
from datetime import date, datetime, time, timedelta, timezone

import pytest

from arena import config, pipeline
from arena.db.client import MemoryDB
from arena.learning.lessons import add_lesson, lesson_score, maintain, score_lessons_for_trade, top_lessons
from arena.learning.postmortem import validate_review
from arena.market_calendar import is_trading_day
from fakes import FakePriceProvider, FakeSession

CFG = config.load("learning")
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def test_new_lessons_start_neutral_and_move_with_evidence():
    assert lesson_score(0, 0) == 0.5
    assert lesson_score(3, 3) == pytest.approx(0.8)
    assert lesson_score(0, 3) == pytest.approx(0.2)


def test_trade_outcome_scores_lessons_used_by_the_opening_decision():
    db = MemoryDB()
    db.insert("lessons", [{"id": 1, "team_id": 7, "text": "a", "times_used": 0, "times_helpful": 0, "score": 0.5, "active": True},
                          {"id": 2, "team_id": 7, "text": "b", "times_used": 0, "times_helpful": 0, "score": 0.5, "active": True}])
    db.insert("decisions", [{"id": 50, "lessons_used": [1]}])
    score_lessons_for_trade(db, {"entry_decision_id": 50, "pnl_pct": 0.05, "benchmark_return": 0.01})
    score_lessons_for_trade(db, {"entry_decision_id": 50, "pnl_pct": -0.02, "benchmark_return": 0.01})
    l1, l2 = db.select("lessons", order="id.asc")
    assert (l1["times_used"], l1["times_helpful"], l1["score"]) == (2, 1, 0.5)
    assert l2["times_used"] == 0                       # not in the prompt -> untouched


def test_duplicates_merge_and_bad_lessons_are_deactivated_not_deleted():
    db = MemoryDB()
    first = add_lesson(db, 7, False, "When volume doubles on a breakout, hold for two weeks.", 1, CFG, NOW)
    dup = add_lesson(db, 7, False, "When volume doubles on a breakout, hold for two weeks!", 2, CFG, NOW)
    assert "added" in first and dup == {"merged_into": first["added"]}
    db.insert("lessons", [{"id": 99, "team_id": 7, "text": "When the Fed speaks, sell everything immediately.",
                           "times_used": 5, "times_helpful": 0, "score": lesson_score(0, 5), "active": True}])
    assert maintain(db, 7, False, CFG)["deactivated"] == 1
    assert len(db.select("lessons")) == 2 and db.select("lessons", {"id": 99})[0]["active"] is False


def test_book_is_capped_at_max_active():
    db = MemoryDB()
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    for i in range(CFG["max_active_per_team"] + 5):
        db.insert("lessons", [{"team_id": 1, "text": f"When {words[i % 10]} {i} {words[(i * 3) % 10]} happens, act {i}.",
                               "times_used": 0, "times_helpful": 0, "score": 0.5 + i / 1000, "active": True}])
    maintain(db, 1, False, {**CFG, "duplicate_similarity": 1.1})
    assert len(db.select("lessons", {"active": True})) == CFG["max_active_per_team"]


def test_only_top_lessons_written_before_the_decision_are_used():
    db = MemoryDB()
    cutoff = datetime(2026, 11, 2, 21, tzinfo=timezone.utc)
    for i, s in enumerate([0.4, 0.9, 0.7]):
        db.insert("lessons", [{"id": i + 1, "team_id": 1, "text": f"l{i}", "score": s, "active": True,
                               "created_at": (cutoff - timedelta(days=1)).isoformat()}])
    db.insert("lessons", [{"id": 9, "team_id": 1, "text": "late", "score": 1.0, "active": True,
                           "created_at": (cutoff + timedelta(minutes=1)).isoformat()}])
    assert [l["id"] for l in top_lessons(db, 1, False, cutoff, 2)] == [2, 3]


@pytest.mark.parametrize("lesson,ok", [
    ("When a stock gaps up more than 5% on earnings, wait one day before buying.", True),
    ("Always do your research before trading anything at all.", False),          # no condition
    ("When unsure, diversify your portfolio across many sectors.", False),        # generic
    ("When volume spikes, buy. Then sell after a week of gains.", False),         # two sentences
])
def test_lessons_must_be_one_specific_actionable_sentence(lesson, ok):
    data = {"what_happened": "It went up a lot.", "right_or_wrong": "Right call.", "what_was_missed": "Nothing.", "lesson": lesson}
    if ok:
        assert validate_review(data).lesson == lesson
    else:
        with pytest.raises(ValueError):
            validate_review(data)


def test_closed_trades_get_post_mortems_and_lessons_reach_later_prompts(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "x test@example.com")
    random.seed(3)
    days = [d for d in (date(2026, 8, 3) + timedelta(days=i) for i in range(100)) if is_trading_day(d)]
    bars = {}
    for k, t in enumerate(config.universe_tickers()):
        px, rows, d = 100.0 + k, [], date(2026, 3, 2)
        while d <= days[-1]:
            if is_trading_day(d):
                o = px * (1 + random.gauss(0, 0.004)); px = o * (1 + random.gauss(0, 0.02))
                rows.append({"ticker": t, "date": d.isoformat(), "open": o, "high": px, "low": px, "close": px, "adj_close": px, "volume": 1e6})
            d += timedelta(days=1)
        bars[t] = rows
    db, prices = MemoryDB(), FakePriceProvider(bars)
    for d in days[:45]:
        pipeline.run(d, db, dry_run=True, session=FakeSession({}), price_provider=prices,
                     now=datetime.combine(d, time(23, 30), timezone.utc))
    closed = db.select("closed_trades")
    assert closed, "expected some trades to close in 45 noisy days"
    assert all(c["entry_decision_id"] for c in closed)
    pms = db.select("post_mortems")
    assert {p["closed_trade_id"] for p in pms} == {c["id"] for c in closed}
    assert db.select("lessons")
    assert any(d["lessons_used"] for d in db.select("decisions"))
    assert any((l["times_used"] or 0) > 0 for l in db.select("lessons"))
