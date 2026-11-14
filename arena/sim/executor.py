"""Database side of the simulator: load/save portfolios, fill pending orders, mark equity,
track the SPY benchmark, and build the leaderboard."""
from collections import defaultdict
from datetime import date, timedelta

from arena import metrics
from arena.sim.portfolio import Portfolio, Position, apply_fill, fill_order

ORDER_EXPIRY_DAYS = 5  # pending orders whose open never shows up in the data are cancelled


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


# ---------- prices ----------
def load_bars(db, tickers: list[str], start: date, end: date) -> dict[str, list[dict]]:
    rows = db.select("prices_daily", {"date": ("gte", start), "ticker": ("in", tickers)},
                     columns="ticker,date,open,close,volume", order="date.asc,ticker.asc")
    out = defaultdict(list)
    for r in rows:
        if _d(r["date"]) <= end:
            out[r["ticker"]].append(r)
    return out


def latest_closes(db, tickers: list[str], d: date) -> dict[str, float]:
    bars = load_bars(db, tickers, d - timedelta(days=14), d)
    return {t: rows[-1]["close"] for t, rows in bars.items() if rows}


# ---------- portfolios ----------
def load_portfolio(db, team_id: int) -> Portfolio:
    p = db.select("portfolios", {"team_id": team_id})[0]
    pf = Portfolio(cash=p["cash"], peak_equity=p.get("peak_equity"), brake_active=bool(p.get("brake_active")))
    for r in db.select("positions", {"team_id": team_id}):
        pf.positions[r["ticker"]] = Position(r["shares"], r["avg_cost"], _d(r["opened_at"]))
    return pf


def save_portfolio(db, team_id: int, pf: Portfolio) -> None:
    db.update("portfolios", {"team_id": team_id},
              {"cash": pf.cash, "peak_equity": pf.peak_equity, "brake_active": pf.brake_active})
    current = {r["ticker"] for r in db.select("positions", {"team_id": team_id}, columns="ticker")}
    for t, p in pf.positions.items():
        db.upsert("positions", [{"team_id": team_id, "ticker": t, "shares": p.shares,
                                 "avg_cost": p.avg_cost, "opened_at": p.opened_at}],
                  on_conflict="team_id,ticker")
    for t in current - set(pf.positions):
        db.delete("positions", {"team_id": team_id, "ticker": t})


# ---------- order execution ----------
def execute_orders(db, team_id: int, run_date: date, risk_cfg: dict, benchmark: str,
                   focus: set[str] | None = None) -> dict:
    """Fill this team's pending orders whose day has come, at that day's open. Buys outside the
    team's focus (e.g. placed before the focus changed) are cancelled instead."""
    summary = {"filled": 0, "cancelled": 0, "waiting": 0}
    orders = db.select("orders", {"team_id": team_id, "status": "pending",
                                  "for_date": ("lte", run_date)}, order="id.asc")
    if focus is not None:
        for o in [o for o in orders if o["side"] == "buy" and o["ticker"] not in focus]:
            db.update("orders", {"id": o["id"]}, {"status": "cancelled", "reason": "outside the team's focus sectors"})
            summary["cancelled"] += 1
        orders = [o for o in orders if not (o["side"] == "buy" and o["ticker"] not in focus)]
    if not orders:
        return summary
    pf = load_portfolio(db, team_id)
    # Sells first so their cash is available to buys on the same morning.
    orders.sort(key=lambda o: (o["side"] != "sell", o["id"]))
    for o in orders:
        for_date = _d(o["for_date"])
        bar = db.select("prices_daily", {"ticker": o["ticker"], "date": for_date}, columns="open")
        if not bar or bar[0]["open"] is None:
            if (run_date - for_date).days > ORDER_EXPIRY_DAYS:
                db.update("orders", {"id": o["id"]}, {"status": "cancelled", "reason": "no opening price"})
                summary["cancelled"] += 1
            else:
                summary["waiting"] += 1
            continue
        f = fill_order(o["side"], o["ticker"], o["shares"], bar[0]["open"], pf,
                       risk_cfg["fee_pct"], risk_cfg["slippage_pct"])
        if f is None:
            db.update("orders", {"id": o["id"]}, {"status": "rejected",
                                                  "reason": "not enough cash or shares at the open"})
            summary["cancelled"] += 1
            continue
        lot = apply_fill(pf, f, for_date)
        db.insert("trades", [{"order_id": o["id"], "team_id": team_id, "ticker": f.ticker, "side": f.side,
                              "shares": f.shares, "fill_price": f.fill_price, "fees": f.fees,
                              "slippage": f.slippage, "executed_at": for_date}])
        db.update("orders", {"id": o["id"]}, {"status": "filled",
                                              "reason": (o.get("reason") or "") + (" (reduced to fit cash)" if f.reduced else "")})
        if lot:
            spy = _spy_return(db, benchmark, lot.entry_date, for_date)
            # The decision that opened the position: its lessons get scored when the trade closes.
            opened = db.select("orders", {"team_id": team_id, "ticker": lot.ticker, "side": "buy",
                                          "status": "filled", "for_date": lot.entry_date},
                               columns="decision_id", order="id.asc", limit=1)
            db.insert("closed_trades", [{
                "team_id": team_id, "ticker": lot.ticker, "shares": lot.shares,
                "entry_date": lot.entry_date, "entry_price": lot.entry_price,
                "exit_date": for_date, "exit_price": lot.exit_price, "pnl": lot.pnl,
                "pnl_pct": lot.pnl_pct, "holding_days": (for_date - lot.entry_date).days,
                "benchmark_return": spy, "exit_decision_id": o.get("decision_id"),
                "entry_decision_id": opened[0]["decision_id"] if opened else None}])
        summary["filled"] += 1
    save_portfolio(db, team_id, pf)
    return summary


def _spy_return(db, benchmark: str, start: date, end: date) -> float | None:
    a = db.select("prices_daily", {"ticker": benchmark, "date": start}, columns="open")
    b = db.select("prices_daily", {"ticker": benchmark, "date": end}, columns="open")
    if a and b and a[0]["open"] and b[0]["open"]:
        return b[0]["open"] / a[0]["open"] - 1
    return None


# ---------- equity, benchmark, leaderboard ----------
def mark_equity(db, team_id: int, run_date: date, closes: dict[str, float], risk_cfg: dict) -> dict:
    from arena.risk.engine import update_brake
    pf = load_portfolio(db, team_id)
    equity = pf.equity(closes)
    update_brake(pf, equity, risk_cfg)
    save_portfolio(db, team_id, pf)
    db.upsert("equity_daily", [{"team_id": team_id, "date": run_date, "cash": pf.cash,
                                "positions_value": pf.value(closes), "total_equity": equity}],
              on_conflict="team_id,date")
    return {"equity": round(equity, 2), "brake": pf.brake_active}


def mark_benchmark(db, run_date: date, benchmark: str, starting_cash: float) -> float | None:
    """SPY bought with the same starting money on the first day, then held."""
    first = db.select("benchmark_daily", order="date.asc", limit=1)
    start = _d(first[0]["date"]) if first else run_date
    c0 = db.select("prices_daily", {"ticker": benchmark, "date": start}, columns="close")
    c1 = latest_closes(db, [benchmark], run_date).get(benchmark)
    if not c0 or not c1:
        return None
    equity = starting_cash * c1 / c0[0]["close"]
    db.upsert("benchmark_daily", [{"date": run_date, "equity": equity}], on_conflict="date")
    return equity


def update_leaderboard(db, run_date: date) -> int:
    teams = db.select("teams", {"status": "active"})
    bench = {_d(r["date"]): r["equity"] for r in db.select("benchmark_daily", order="date.asc")}
    starts = []
    for t in teams:
        eq = db.select("equity_daily", {"team_id": t["id"]}, columns="date,total_equity", order="date.asc")
        if not eq:
            continue
        series = [r["total_equity"] for r in eq]
        start = _d(eq[0]["date"])
        starts.append(start)
        trades = db.select("trades", {"team_id": t["id"]}, columns="id", order="id.asc")
        db.upsert("leaderboard", [{
            "key": f"team:{t['id']}", "team_id": t["id"], "name": t["name"], "is_benchmark": False,
            "start_date": start, "as_of": run_date, "equity": series[-1],
            "total_return": metrics.total_return(series), "sharpe": metrics.sharpe(series),
            "max_drawdown": metrics.max_drawdown(series), "trades": len(trades)}], on_conflict="key")
    if starts and bench:
        s0 = min(starts)
        series = [e for d, e in sorted(bench.items()) if d >= s0]
        if series:
            db.upsert("leaderboard", [{
                "key": "SPY", "team_id": None, "name": "SPY (buy & hold)", "is_benchmark": True,
                "start_date": s0, "as_of": run_date, "equity": series[-1],
                "total_return": metrics.total_return(series), "sharpe": metrics.sharpe(series),
                "max_drawdown": metrics.max_drawdown(series), "trades": 1}], on_conflict="key")
    return len(teams)


def next_order_date(run_date: date) -> date:
    from arena.market_calendar import next_trading_day
    return next_trading_day(run_date)

