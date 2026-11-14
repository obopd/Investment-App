from datetime import date, datetime, time, timezone

from arena import config, pipeline
from arena.agents.team import validate_analyst
from arena.db.client import MemoryDB
from arena.sim.executor import execute_orders
from fakes import FakePriceProvider, FakeSession

TEAMS = {t["name"]: t for t in config.load("teams")["teams"]}


def test_focus_lists():
    assert "AAPL" in config.focus_tickers(TEAMS["A"]) and "JPM" not in config.focus_tickers(TEAMS["A"])
    assert "JPM" in config.focus_tickers(TEAMS["C"]) and "GOOGL" not in config.focus_tickers(TEAMS["C"])
    assert len(config.focus_tickers(TEAMS["B"])) == 30


def test_analyst_can_sell_but_not_buy_outside_focus():
    v = validate_analyst(3, {"GOOGL", "JPM"}, set(), held={"GOOGL"}, focus={"JPM"})
    _, cands = v({"market_summary": "x", "candidates": [
        {"ticker": "GOOGL", "action": "add", "report": "r", "fact_card_ids": []},
        {"ticker": "GOOGL", "action": "sell", "report": "r", "fact_card_ids": []},
        {"ticker": "JPM", "action": "buy", "report": "r", "fact_card_ids": []}]})
    assert [(c.ticker, c.action) for c in cands] == [("GOOGL", "sell"), ("JPM", "buy")]   # the add is dropped
    _, cands = v({"market_summary": "x", "candidates": [
        {"ticker": "GOOGL", "action": "sell", "report": "r", "fact_card_ids": []}]})
    assert [(c.ticker, c.action) for c in cands] == [("GOOGL", "sell")]


def test_pending_buys_outside_focus_are_cancelled_not_filled():
    db = MemoryDB()
    db.insert("portfolios", [{"team_id": 1, "cash": 100_000}])
    db.insert("prices_daily", [{"ticker": "GOOGL", "date": "2026-11-16", "open": 300.0}])
    db.insert("orders", [{"id": 9, "team_id": 1, "ticker": "GOOGL", "side": "buy", "shares": 6,
                          "status": "pending", "for_date": "2026-11-16"}])
    s = execute_orders(db, 1, date(2026, 11, 16), config.load("risk"), "SPY", focus={"JPM"})
    assert s["cancelled"] == 1 and db.select("orders")[0]["status"] == "cancelled" and db.select("trades") == []


def test_practice_teams_only_buy_inside_their_focus(monkeypatch):
    import test_daily_simulation as sim
    monkeypatch.setenv("SEC_USER_AGENT", "x test@example.com")
    db, prices = MemoryDB(), FakePriceProvider(sim.fake_bars())
    for d in sim.DAYS:
        pipeline.run(d, db, dry_run=True, session=FakeSession({}), price_provider=prices,
                     now=datetime.combine(d, time(23, 30), timezone.utc))
    teams = {t["id"]: t for t in db.select("teams")}
    buys = [o for o in db.select("orders") if o["side"] == "buy"]
    assert buys
    for o in buys:
        assert o["ticker"] in config.focus_tickers(teams[o["team_id"]]["config"]), o
