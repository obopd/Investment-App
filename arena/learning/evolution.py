"""Monthly evolution: retire the worst team, add a mutated copy of the best one.

Safeguards: only in live mode (configurable), only on the first trading day of a month, at most
once per month, and only when every active team has enough live history and closed trades.
Ranking uses the Sharpe ratio of daily returns in excess of SPY, not raw profit.
"""
import math
import random
import re
from datetime import date, datetime

from arena.market_calendar import is_trading_day

TRADING_DAYS = 252


def first_trading_day_of_month(d: date) -> bool:
    if not is_trading_day(d):
        return False
    probe = date(d.year, d.month, 1)
    while not is_trading_day(probe):
        probe = date.fromordinal(probe.toordinal() + 1)
    return probe == d


def excess_sharpe(team_equity: dict, bench_equity: dict) -> float | None:
    days = sorted(d for d in team_equity if d in bench_equity)
    ex = []
    for a, b in zip(days, days[1:]):
        if team_equity[a] and bench_equity[a]:
            ex.append((team_equity[b] / team_equity[a] - 1) - (bench_equity[b] / bench_equity[a] - 1))
    if len(ex) < 5:
        return None
    m = sum(ex) / len(ex)
    var = sum((x - m) ** 2 for x in ex) / (len(ex) - 1)
    return m / math.sqrt(var) * math.sqrt(TRADING_DAYS) if var > 0 else None


def mutate(config: dict, space: dict, count_range, rng: random.Random) -> tuple[dict, list[str]]:
    child, notes = dict(config), []
    keys = [k for k in space if k in child or "choices" in space[k]]
    n = rng.randint(*count_range)
    for key in rng.sample(keys, min(n, len(keys))):
        rule = space[key]
        old = child.get(key)
        if "choices" in rule:
            options = [c for c in rule["choices"] if c != old] or rule["choices"]
            new = rng.choice(options)
        else:
            new = old + rng.choice([-1, 1]) * rule["step"]
            if new < rule["min"] or new > rule["max"]:   # bounce off the limit instead of clamping
                new = old - (new - old)
            new = round(min(max(new, rule["min"]), rule["max"]), 4)
            if isinstance(rule["step"], int):
                new = int(new)
        child[key] = new
        notes.append(f"{key}: {old} -> {new}")
    return child, notes


def base_name(name: str) -> str:
    return re.sub(r"( g\d+)?( \(practice\))?$", "", name)


def _d(v):
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def maybe_evolve(db, run_date: date, mode: str, cfg: dict, starting_cash: float, now: datetime) -> dict:
    if not cfg.get("enabled", True):
        return {"skipped": "disabled"}
    if mode == "practice" and not cfg.get("allow_in_practice", False):
        return {"skipped": "practice mode"}
    if not first_trading_day_of_month(run_date):
        return {"skipped": "not the first trading day of the month"}
    teams = [t for t in db.select("teams", {"status": "active"}, order="id.asc") if (t.get("config") or {}).get("mode") == mode]
    if len(teams) < 2:
        return {"skipped": "fewer than 2 active teams"}
    month = run_date.strftime("%Y-%m")
    for t in db.select("teams", order="id.asc"):
        if t.get("parent_team_id") and str(t.get("created_at", ""))[:7] == month and (t.get("config") or {}).get("mode") == mode:
            return {"skipped": "already evolved this month"}

    bench = {_d(r["date"]): r["equity"] for r in db.select("benchmark_daily", order="date.asc")}
    scores = {}
    for t in teams:
        eq = {_d(r["date"]): r["total_equity"] for r in db.select("equity_daily", {"team_id": t["id"]}, order="date.asc")}
        closed = db.select("closed_trades", {"team_id": t["id"]}, columns="id", order="id.asc")
        if len(eq) < cfg["min_trading_days"]:
            return {"skipped": f"{t['name']} has {len(eq)} trading days (needs {cfg['min_trading_days']})"}
        if len(closed) < cfg["min_closed_trades"]:
            return {"skipped": f"{t['name']} has {len(closed)} closed trades (needs {cfg['min_closed_trades']})"}
        s = excess_sharpe(eq, bench)
        if s is None:
            return {"skipped": f"not enough overlapping benchmark data for {t['name']}"}
        scores[t["id"]] = s

    ranked = sorted(teams, key=lambda t: scores[t["id"]])
    worst, best = ranked[0], ranked[-1]
    rng = random.Random(f"{run_date.isoformat()}-{best['id']}")   # reproducible mutations
    parent_cfg = {k: v for k, v in best["config"].items() if k != "mode"}
    child_cfg, notes = mutate(parent_cfg, cfg["mutations"], cfg.get("mutate_count", [1, 2]), rng)
    child_cfg["mode"] = mode
    gen = (best.get("generation") or 0) + 1

    db.update("teams", {"id": worst["id"]}, {"status": "retired", "retired_at": now})
    db.update("orders", {"team_id": worst["id"], "status": "pending"}, {"status": "cancelled", "reason": "team retired by evolution"})
    name = f"{base_name(best['name'])} g{gen}" + (" (practice)" if mode == "practice" else "")
    child = db.insert("teams", [{"name": name, "generation": gen, "parent_team_id": best["id"],
                                 "mutation": "; ".join(notes), "config": child_cfg, "status": "active",
                                 "created_at": now}], returning=True)[0]
    db.insert("portfolios", [{"team_id": child["id"], "cash": starting_cash, "peak_equity": starting_cash,
                              "start_date": run_date}])
    # The child inherits the parent's active lesson book (copied, so the books evolve separately).
    inherited = db.select("lessons", {"team_id": best["id"], "active": True}, order="id.asc")
    if inherited:
        db.insert("lessons", [{"team_id": child["id"], "text": l["text"], "post_mortem_id": l.get("post_mortem_id"),
                               "times_used": l["times_used"], "times_helpful": l["times_helpful"],
                               "score": l["score"], "active": True, "created_at": now} for l in inherited])
    return {"retired": worst["name"], "parent": best["name"], "child": name, "mutation": notes,
            "excess_sharpe": {t["name"]: round(scores[t["id"]], 3) for t in teams},
            "lessons_inherited": len(inherited)}
