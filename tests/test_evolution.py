import random
from datetime import date, datetime, timedelta, timezone

import pytest

from arena import config, pipeline
from arena.db.client import MemoryDB
from arena.learning.evolution import base_name, excess_sharpe, first_trading_day_of_month, maybe_evolve, mutate
from arena.market_calendar import is_trading_day

CFG = config.load("evolution")
NOW = datetime(2027, 2, 1, 23, 30, tzinfo=timezone.utc)
RUN = date(2027, 2, 1)


def test_first_trading_day_of_month():
    assert first_trading_day_of_month(date(2027, 2, 1))
    assert not first_trading_day_of_month(date(2027, 2, 2))
    assert first_trading_day_of_month(date(2026, 8, 3))        # Aug 1-2 2026 is a weekend
    assert not first_trading_day_of_month(date(2026, 8, 1))


def test_mutation_changes_one_or_two_settings_within_bounds():
    parent = {"news_weight": 1.0, "min_source_trust": 0.5, "min_conviction": 0.6, "max_candidates_per_day": 1,
              "fact_card_lookback_days": 7, "holding_style": "short", "models": {"judge": "strong"}}
    for seed in range(30):
        child, notes = mutate(parent, CFG["mutations"], CFG["mutate_count"], random.Random(seed))
        changed = [k for k in parent if child[k] != parent[k]]
        assert 1 <= len(changed) <= 2 and len(notes) == len(changed)
        assert 0 <= child["news_weight"] <= 1 and 1 <= child["max_candidates_per_day"] <= 5
        assert child["models"] == parent["models"]
    assert base_name("Momentum g2 (practice)") == "Momentum" and base_name("Steady") == "Steady"


def test_excess_sharpe_rewards_beating_spy():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
    random.seed(1)
    bench = {d: 100 * (1.0005 ** i) for i, d in enumerate(days)}
    good = {d: bench[d] * (1.001 ** i) * (1 + random.gauss(0, 0.001)) for i, d in enumerate(days)}
    bad = {d: bench[d] * (0.999 ** i) * (1 + random.gauss(0, 0.001)) for i, d in enumerate(days)}
    assert excess_sharpe(good, bench) > 0 > excess_sharpe(bad, bench)


def world(days=70, trades=12, mode="live"):
    db = MemoryDB()
    dates = []
    d = RUN - timedelta(days=1)
    while len(dates) < days:
        if is_trading_day(d):
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()
    db.insert("benchmark_daily", [{"date": x.isoformat(), "equity": 100_000 * (1.0005 ** i)} for i, x in enumerate(dates)])
    random.seed(2)
    for tid, (name, drift) in enumerate([("Momentum", 0.001), ("Newshound", -0.001), ("Steady", 0.0003)], start=1):
        db.insert("teams", [{"id": tid, "name": name, "generation": 0, "status": "active",
                             "config": {"mode": mode, "news_weight": 0.3, "min_source_trust": 0.4, "min_conviction": 0.6,
                                        "max_candidates_per_day": 3, "fact_card_lookback_days": 7, "holding_style": "short"}}])
        db.insert("equity_daily", [{"team_id": tid, "date": x.isoformat(),
                                    "total_equity": 100_000 * ((1.0005 + drift) ** i) * (1 + random.gauss(0, 0.002))}
                                   for i, x in enumerate(dates)])
        db.insert("closed_trades", [{"team_id": tid, "ticker": "AAPL"} for _ in range(trades)])
        db.insert("lessons", [{"team_id": tid, "text": f"When {name} sees X, do Y.", "times_used": 2, "times_helpful": 1,
                               "score": 0.5, "active": True}])
    db.insert("orders", [{"team_id": 2, "ticker": "AAPL", "side": "buy", "shares": 1, "status": "pending"}])
    return db


def test_worst_retires_and_best_spawns_a_mutated_child_with_lineage():
    db = world()
    res = maybe_evolve(db, RUN, "live", CFG, 100_000, NOW)
    assert res["retired"] == "Newshound" and res["parent"] == "Momentum" and res["child"] == "Momentum g1"
    child = db.select("teams", {"name": "Momentum g1"})[0]
    assert child["parent_team_id"] == 1 and child["generation"] == 1 and child["mutation"]
    assert child["config"]["mode"] == "live"
    assert db.select("teams", {"id": 2})[0]["status"] == "retired"
    assert db.select("orders", {"team_id": 2})[0]["status"] == "cancelled"
    assert db.select("portfolios", {"team_id": child["id"]})[0]["cash"] == 100_000
    assert [l["text"] for l in db.select("lessons", {"team_id": child["id"]})] == ["When Momentum sees X, do Y."]
    assert maybe_evolve(db, RUN, "live", CFG, 100_000, NOW)["skipped"] == "already evolved this month"


@pytest.mark.parametrize("kwargs,run,mode,reason", [
    ({}, RUN, "practice", "practice mode"),
    ({}, date(2027, 2, 2), "live", "not the first trading day"),
    ({"days": 30}, RUN, "live", "trading days"),
    ({"trades": 3}, RUN, "live", "closed trades"),
])
def test_safeguards(kwargs, run, mode, reason):
    db = world(mode=mode, **kwargs)
    assert reason in maybe_evolve(db, run, mode, CFG, 100_000, NOW)["skipped"]
    assert len(db.select("teams", {"status": "active"})) == 3


def test_three_founding_teams_are_seeded_and_evolved_children_are_kept():
    db = MemoryDB()
    teams = pipeline.seed_teams(db, RUN, "live", 100_000)
    assert [t["name"] for t in teams] == ["Momentum", "Newshound", "Steady"]
    # Evolution retired Newshound and added a child: the founder must not come back.
    nh = next(t for t in teams if t["name"] == "Newshound")
    db.update("teams", {"id": nh["id"]}, {"status": "retired"})
    db.insert("teams", [{"name": "Momentum g1", "generation": 1, "status": "active", "config": {"mode": "live"}}])
    again = pipeline.seed_teams(db, RUN, "live", 100_000)
    assert sorted(t["name"] for t in again) == ["Momentum", "Momentum g1", "Steady"]
