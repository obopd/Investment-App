"""Daily entry point. Run after US market close:

    python -m arena.pipeline                 # today (New York date)
    python -m arena.pipeline --date 2026-11-02
    python -m arena.pipeline --dry-run       # in-memory database, practice AI, nothing saved

Steps (CLAUDE.md Section 6): budget check -> prices -> fill yesterday's orders at today's open
-> news and filings -> fact cards -> equity, benchmark, leaderboard -> team decisions -> log.
Trust scoring (Phase 4) and post-mortems (Phase 3) slot in later.
Every step is safe to re-run: writes are upserts, skip-if-exists, or status changes.
"""
import argparse
import json
import sys
import traceback
from datetime import date, datetime

from arena import config
from arena.agents.team import run_team
from arena.data.common import utcnow
from arena.data.filings import collect_filings, require_user_agent
from arena.data.news import collect_news
from arena.data.prices import TiingoProvider, collect_prices, history_start_from_config
from arena.data.tagging import TickerTagger
from arena.db.client import MemoryDB, SupabaseDB
from arena.facts.extract import extract_fact_cards
from arena.http import make_session
from arena.llm.budget import BudgetGuard
from arena.llm.client import make_llm
from arena.lookahead import NEW_YORK
from arena.market_calendar import is_trading_day
from arena.sim import executor


def seed_stocks(db) -> dict:
    u = config.load("universe")
    rows = [{"ticker": s["ticker"], "name": s["name"], "sector": s["sector"], "active": True}
            for s in u["stocks"]]
    rows.append({"ticker": u["benchmark"], "name": "SPDR S&P 500 ETF (benchmark)",
                 "sector": "Benchmark", "active": True})
    db.upsert("stocks", rows, on_conflict="ticker")
    # Stocks removed from the config are deactivated, never deleted (history stays valid).
    keep = {r["ticker"] for r in rows}
    for s in db.select("stocks", {"active": True}, columns="ticker"):
        if s["ticker"] not in keep:
            db.update("stocks", {"ticker": s["ticker"]}, {"active": False})
    return {"stocks": len(rows)}


def seed_teams(db, run_date: date, mode: str, starting_cash: float) -> list[dict]:
    """Create configured teams for the current mode; retire teams from the other mode.
    Practice and live never mix: going live starts fresh teams with fresh imaginary money."""
    wanted = config.load("teams")["teams"]
    active = db.select("teams", {"status": "active"})
    for t in active:
        if (t.get("config") or {}).get("mode") != mode:
            db.update("teams", {"id": t["id"]}, {"status": "retired", "retired_at": utcnow()})
    teams = []
    for cfg in wanted:
        name = cfg["name"] + (" (practice)" if mode == "practice" else "")
        found = [t for t in db.select("teams", {"name": name, "status": "active"})]
        if found:
            team = found[0]
            if team["config"] != {**cfg, "mode": mode}:   # config edits apply from the next run
                db.update("teams", {"id": team["id"]}, {"config": {**cfg, "mode": mode}})
                team["config"] = {**cfg, "mode": mode}
        else:
            team = db.insert("teams", [{"name": name, "generation": 0, "config": {**cfg, "mode": mode},
                                        "status": "active"}], returning=True)[0]
            db.insert("portfolios", [{"team_id": team["id"], "cash": starting_cash,
                                      "peak_equity": starting_cash, "start_date": run_date}])
        teams.append(team)
    return teams


def run(run_date: date, db, *, dry_run: bool = False, force: bool = False,
        session=None, price_provider=None, now: datetime | None = None,
        skip_collect: bool = False) -> dict:
    """Run one day. Dependencies are injectable so tests can run fully offline."""
    now = now or utcnow()
    if not is_trading_day(run_date) and not force:
        db.insert("runs", [{"run_date": run_date, "status": "skipped", "dry_run": dry_run,
                            "finished_at": now, "summary": {"reason": "market closed"}}])
        return {"status": "skipped", "reason": "market closed"}

    run_row = db.insert("runs", [{"run_date": run_date, "status": "running", "dry_run": dry_run,
                                  "started_at": now}], returning=True)[0]
    session = session or make_session()
    summary: dict = {}
    errors: dict = {}

    def step(name, fn):
        try:
            summary[name] = fn()
            if isinstance(summary[name], dict) and summary[name].get("errors"):
                errors[name] = "some items failed (see summary)"
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"[:500]
            summary[name] = {"failed": True}
            traceback.print_exc()

    universe = config.load("universe")
    stocks = [s["ticker"] for s in universe["stocks"]]
    bench = universe["benchmark"]
    risk_cfg = config.load("risk")
    models_cfg = config.load("models")
    mode = "practice" if dry_run else models_cfg.get("mode", "practice")

    step("stocks", lambda: seed_stocks(db))
    teams: list[dict] = []

    def _teams():
        teams.extend(seed_teams(db, run_date, mode, risk_cfg["starting_cash"]))
        return {"active": [t["name"] for t in teams]}
    step("teams", _teams)

    # 1. Budget check. Over the cap means AI steps are skipped; everything else still runs.
    guard = BudgetGuard.from_db(db, config.load("budget"), now)
    llm = make_llm(db, run_row["id"], models_cfg, guard, dry_run)
    if guard.exhausted:
        print("WARNING: monthly AI budget reached; AI steps will be skipped.")

    # 2. Prices first: today's open is needed to fill yesterday's orders.
    if not skip_collect:
        def prices():
            cfg = config.load("prices")
            provider = price_provider or TiingoProvider.from_env(session, cfg.get("request_delay_seconds", 1.5))
            return collect_prices(db, provider, config.universe_tickers(), run_date,
                                  history_start_from_config(cfg))
        step("prices", prices)

    # 3. Fill pending orders at today's open.
    step("orders_filled", lambda: {t["name"]: executor.execute_orders(db, t["id"], run_date, risk_cfg, bench)
                                   for t in teams})

    # 4. News and filings.
    if not skip_collect:
        def news():
            cfg = config.load("news")
            return collect_news(db, session, cfg["feeds"], TickerTagger(universe["stocks"]), now,
                                cfg.get("max_items_per_feed", 50), cfg.get("snippet_chars", 500))
        step("news", news)

        def filings():
            cfg = config.load("filings")
            session.headers["User-Agent"] = require_user_agent()  # SEC requires contact info
            return collect_filings(db, session, stocks, now, cfg["forms"], cfg["lookback_days"],
                                   cfg["lookback_days_first_run"], cfg.get("request_delay_seconds", 0.2))
        step("filings", filings)

    # 5. Fact cards (cheap AI model).
    if llm.available:
        step("fact_cards", lambda: extract_fact_cards(db, llm, set(stocks), config.load("facts"), now))

    # 6. Equity for every team, the SPY benchmark, and the leaderboard.
    def equity():
        closes = executor.latest_closes(db, stocks + [bench], run_date)
        out = {t["name"]: executor.mark_equity(db, t["id"], run_date, closes, risk_cfg) for t in teams}
        out["SPY"] = executor.mark_benchmark(db, run_date, bench, risk_cfg["starting_cash"])
        executor.update_leaderboard(db, run_date)
        return out
    step("equity", equity)

    # 7. Team decisions -> orders for the next trading day's open.
    step("decisions", lambda: {t["name"]: run_team(db, llm, t, run_date, stocks, bench, risk_cfg, run_row["id"])
                               for t in teams})

    summary["ai"] = {"mode": mode, "available": llm.available, "calls": llm.calls,
                     "failures": llm.failures[:10], **guard.summary()}
    if llm.failures:
        errors["ai"] = f"{len(llm.failures)} AI call(s) failed validation or errored"

    collection_steps = [s for s in ("prices", "news", "filings") if s in summary]
    hard_failures = [s for s in collection_steps if summary.get(s, {}).get("failed")]
    if collection_steps and len(hard_failures) == len(collection_steps):
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "ok"
    db.update("runs", {"id": run_row["id"]}, {
        "status": status, "finished_at": utcnow(), "summary": json.loads(json.dumps(summary, default=str)),
        "error": json.dumps(errors)[:2000] if errors else None})
    return {"status": status, "summary": summary, "errors": errors}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AI Trading Arena daily pipeline")
    p.add_argument("--date", help="run as if it were this New York date (YYYY-MM-DD)")
    p.add_argument("--dry-run", action="store_true", help="in-memory DB and practice AI; saves nothing")
    p.add_argument("--force", action="store_true", help="run even if the market is closed")
    args = p.parse_args(argv)

    run_date = date.fromisoformat(args.date) if args.date else datetime.now(NEW_YORK).date()
    db = MemoryDB() if args.dry_run else SupabaseDB.from_env()
    result = run(run_date, db, dry_run=args.dry_run, force=args.force)
    print(json.dumps({"date": run_date.isoformat(), **result}, indent=2, default=str))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
