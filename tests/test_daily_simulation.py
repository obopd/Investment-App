"""Several trading days through the full pipeline, offline, in practice mode."""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from arena import config, pipeline
from arena.db.client import MemoryDB
from arena.market_calendar import is_trading_day, next_trading_day
from fakes import FakePriceProvider, FakeSession

DAYS = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)]


def fake_bars():
    tickers = config.universe_tickers()
    bars = {}
    for k, t in enumerate(tickers):
        px, rows, d = 100.0 + k, [], date(2026, 6, 1)
        drift = (k - len(tickers) / 2) / 2000     # some tickers trend up, some down
        while d <= DAYS[-1]:
            if is_trading_day(d):
                open_ = px * 1.001
                px = open_ * (1 + drift)
                rows.append({"ticker": t, "date": d.isoformat(), "open": open_, "high": px, "low": px,
                             "close": px, "adj_close": px, "volume": 1_000_000})
            d += timedelta(days=1)
        bars[t] = rows
    return bars


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "AI-Trading-Arena test@example.com")
    return MemoryDB(), FakePriceProvider(fake_bars())


def run_day(db, prices, d):
    now = datetime.combine(d, time(23, 30), timezone.utc)
    return pipeline.run(d, db, dry_run=True, session=FakeSession({}), price_provider=prices, now=now)


def test_orders_fill_at_next_open_with_costs_and_equity_tracks(world):
    db, prices = world
    run_day(db, prices, DAYS[0])
    orders = db.select("orders")
    assert orders and all(o["status"] == "pending" and o["for_date"] == DAYS[1].isoformat() for o in orders)
    assert all(o["side"] == "buy" for o in orders)
    assert db.select("trades") == []                          # nothing fills on decision day

    run_day(db, prices, DAYS[1])
    trades = db.select("trades")
    assert len(trades) == len(orders) and all(t["executed_at"] == DAYS[1].isoformat() for t in trades)
    risk = config.load("risk")
    for t in trades:
        open_ = next(b["open"] for b in prices.bars[t["ticker"]] if b["date"] == DAYS[1].isoformat())
        assert t["fill_price"] == pytest.approx(open_ * (1 + risk["slippage_pct"]))
        assert t["fees"] == pytest.approx(t["shares"] * t["fill_price"] * risk["fee_pct"])

    team = db.select("teams")[0]
    assert team["name"] == "Momentum (practice)"
    eq = db.select("equity_daily", {"team_id": team["id"]}, order="date.asc")
    assert [r["date"] for r in eq] == [DAYS[0].isoformat(), DAYS[1].isoformat()]
    assert eq[0]["total_equity"] == risk["starting_cash"]
    pf = db.select("portfolios")[0]
    weights = [p["shares"] * p["avg_cost"] / risk["starting_cash"] for p in db.select("positions")]
    assert pf["cash"] >= 0 and all(w <= risk["max_weight_per_stock"] + 1e-9 for w in weights)


def test_full_week_rerun_safety_leaderboard_and_logging(world):
    db, prices = world
    for d in DAYS:
        run_day(db, prices, d)
    counts = {t: len(db.select(t)) for t in ("decisions", "orders", "trades", "equity_daily", "benchmark_daily")}
    run_day(db, prices, DAYS[-1])                             # same day again
    assert {t: len(db.select(t)) for t in counts} == counts

    board = {r["key"]: r for r in db.select("leaderboard")}
    assert "SPY" in board and any(k.startswith("team:") for k in board)
    assert board["SPY"]["start_date"] == DAYS[0].isoformat()
    assert all(r["as_of"] == DAYS[-1].isoformat() for r in board.values())
    assert len(db.select("benchmark_daily")) == len(DAYS)

    usage = db.select("llm_usage")
    assert usage and all(u["cost_usd"] == 0 and u["provider"] == "mock" for u in usage)
    roles = {u["role"] for u in usage}
    assert {"analyst", "bull", "bear", "judge"} <= roles
    for dec in db.select("decisions"):
        if dec["judge_verdict"]:
            assert set(dec["judge_verdict"]) == {"ticker", "action", "conviction", "target_weight",
                                                  "reasoning", "key_evidence", "main_risk"}
            assert dec["risk_result"]["status"] in ("approved", "resized", "vetoed", "hold")
    last = db.select("runs", order="id.desc", limit=1)[0]
    assert last["summary"]["ai"]["mode"] == "practice"


def test_going_live_retires_practice_team(world, monkeypatch):
    db, prices = world
    run_day(db, prices, DAYS[0])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    live = {**config.load("models"), "mode": "live"}
    monkeypatch.setattr(config, "load", lambda name, _orig=config.load: live if name == "models" else _orig(name))
    now = datetime.combine(DAYS[1], time(23, 30), timezone.utc)
    result = pipeline.run(DAYS[1], db, dry_run=False, session=FakeSession({}), price_provider=prices, now=now)
    teams = {t["name"]: t["status"] for t in db.select("teams")}
    assert teams == {"Momentum (practice)": "retired", "Momentum": "active"}
    practice_id = next(t["id"] for t in db.select("teams") if t["status"] == "retired")
    assert all(o["status"] != "pending" for o in db.select("orders", {"team_id": practice_id}))
    assert result["summary"]["ai"]["available"] is False       # no key: AI skipped, data still collected
    assert any(d["final_action"].startswith("no AI") for d in db.select("decisions"))
    assert next_trading_day(DAYS[0]) == DAYS[1]


def test_no_decisions_before_the_close(world):
    db, prices = world
    midday = datetime.combine(DAYS[0], time(17, 0), timezone.utc)   # 12pm New York
    r = pipeline.run(DAYS[0], db, dry_run=True, session=FakeSession({}), price_provider=prices, now=midday)
    assert "market still open" in r["summary"]["decisions"]["skipped"]
    assert db.select("decisions") == []
    run_day(db, prices, DAYS[0])                                     # the evening run still decides
    assert db.select("decisions")
