"""Source trust: did a source's claims come true?

Each source keeps Beta counts: alpha = correct + 1, beta = incorrect + 1,
trust = alpha / (alpha + beta). New sources start at 0.5. "Unclear" outcomes change nothing.
A price move doesn't prove a claim true or false; trust only means something over many claims.
Practice-mode (placeholder) cards are never scored, so practice never moves real trust.
"""
from datetime import date, datetime, time, timedelta

from arena.lookahead import NEW_YORK, to_utc
from arena.market_calendar import is_trading_day, next_trading_day, previous_trading_day

OPPOSITE = {"positive": "negative", "negative": "positive"}


def base_date(published) -> date:
    """The last trading day whose close came before the claim was published."""
    ny = to_utc(published).astimezone(NEW_YORK)
    d = ny.date()
    if is_trading_day(d) and ny.time() >= time(16, 0):
        return d
    return previous_trading_day(d)


def horizon_end(start: date, horizon_days: int) -> date:
    d = start
    for _ in range(horizon_days):
        d = next_trading_day(d)
    return d


def judge(direction: str, excess: float | None, threshold: float) -> str:
    if excess is None or direction not in OPPOSITE:
        return "unclear"
    signed = excess if direction == "positive" else -excess
    if signed > threshold:
        return "correct"
    if signed < -threshold:
        return "incorrect"
    return "unclear"


def _close(db, ticker: str, d: date) -> float | None:
    r = db.select("prices_daily", {"ticker": ticker, "date": d}, columns="close,adj_close")
    if not r:
        return None
    return r[0]["adj_close"] or r[0]["close"]   # adjusted, so dividends don't count as moves


def excess_return(db, ticker: str, benchmark: str, start: date, end: date) -> float | None:
    s0, s1 = _close(db, ticker, start), _close(db, ticker, end)
    b0, b1 = _close(db, benchmark, start), _close(db, benchmark, end)
    if None in (s0, s1, b0, b1) or not s0 or not b0:
        return None
    return (s1 / s0 - 1) - (b1 / b0 - 1)


def _sources_of(db, card_id: int) -> list[int]:
    return sorted({l["source_id"] for l in db.select("fact_card_sources", {"fact_card_id": card_id},
                                                     columns="source_id", order="article_id.asc")})


def _apply(db, source_ids: list[int], outcome: str) -> None:
    if outcome not in ("correct", "incorrect"):
        return
    for sid in source_ids:
        s = db.select("sources", {"id": sid})[0]
        a = (s.get("trust_alpha") or 1) + (outcome == "correct")
        b = (s.get("trust_beta") or 1) + (outcome == "incorrect")
        db.update("sources", {"id": sid}, {"trust_alpha": a, "trust_beta": b, "trust_score": a / (a + b),
                                           "claims_evaluated": (s.get("claims_evaluated") or 0) + 1})


def evaluate_claims(db, run_date: date, cfg: dict, benchmark: str, now: datetime) -> dict:
    summary = {"correct": 0, "incorrect": 0, "unclear": 0, "contradicted": 0, "waiting": 0}
    done = {e["fact_card_id"] for e in db.select("claim_evaluations", columns="fact_card_id", order="fact_card_id.asc")}
    since = now - timedelta(days=120)   # anything older was settled long ago
    cards = [c for c in db.select("fact_cards", {"practice": False, "published_at": ("gte", since)}, order="id.asc")
             if c["id"] not in done and c.get("published_at")]
    domains = {s["id"]: s["domain"] for s in db.select("sources", columns="id,domain", order="id.asc")}
    official = set(cfg.get("official_domains", []))

    def is_official(card_id):
        return any(domains.get(s) in official for s in _sources_of(db, card_id))

    by_ticker: dict[str, list[dict]] = {}
    for c in db.select("fact_cards", {"practice": False, "published_at": ("gte", since)}, order="id.asc"):
        by_ticker.setdefault(c["ticker"], []).append(c)

    for c in cards:
        sources = _sources_of(db, c["id"])
        outcome, excess = None, None
        # 1) An official statement saying the opposite settles it.
        if c["direction"] in OPPOSITE and not is_official(c["id"]):
            window_end = to_utc(c["published_at"]) + timedelta(days=c["horizon_days"] * 2)
            for o in by_ticker.get(c["ticker"], []):
                if (o["id"] != c["id"] and o["direction"] == OPPOSITE[c["direction"]]
                        and o["claim_type"] == c["claim_type"]
                        and (o.get("confidence") or 0) >= cfg.get("official_min_confidence", 0.6)
                        and to_utc(c["published_at"]) < to_utc(o["published_at"]) <= min(window_end, now)
                        and is_official(o["id"])):
                    outcome = "incorrect"
                    summary["contradicted"] += 1
                    break
        # 2) Otherwise wait for the horizon and compare with SPY.
        if outcome is None:
            if c["ticker"] is None:
                outcome = "unclear"   # macro claims have no single stock to check against SPY
            else:
                start = base_date(c["published_at"])
                end = horizon_end(start, c["horizon_days"])
                if end > run_date:
                    summary["waiting"] += 1
                    continue
                excess = excess_return(db, c["ticker"], benchmark, start, end)
                if excess is None and (run_date - end).days < 10:
                    summary["waiting"] += 1   # prices may still arrive
                    continue
                threshold = cfg["thresholds"].get(c["horizon_days"], cfg["thresholds"].get(str(c["horizon_days"]), 0.01))
                outcome = judge(c["direction"], excess, threshold)
        db.insert("claim_evaluations", [{"fact_card_id": c["id"], "evaluated_at": now,
                                         "excess_return": excess, "outcome": outcome}])
        _apply(db, sources, outcome)
        summary[outcome] += 1
    return summary


def corroborate(db, cfg: dict, now: datetime) -> dict:
    """Label recent claims repeated by several independent, not-distrusted sources."""
    since = now - timedelta(days=cfg.get("corroboration_window_days", 7))
    cards = db.select("fact_cards", {"published_at": ("gte", since)}, columns="id,corroborated", order="id.asc")
    trust = {s["id"]: (s["trust_score"] if s.get("trust_score") is not None else 0.5)
             for s in db.select("sources", columns="id,trust_score", order="id.asc")}
    changed = 0
    for c in cards:
        ok = [s for s in _sources_of(db, c["id"]) if trust.get(s, 0.5) >= cfg["corroboration_min_trust"]]
        flag = len(ok) >= cfg["corroboration_min_sources"]
        if bool(c.get("corroborated")) != flag:
            db.update("fact_cards", {"id": c["id"]}, {"corroborated": flag})
            changed += 1
    return {"recent_cards": len(cards), "changed": changed}
