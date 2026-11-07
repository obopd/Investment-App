"""Everything a team sees, prepared by code. The no-look-ahead rule is enforced here:
fact cards must be published before the decision day's close, prices end on the decision day."""
import math
from datetime import date, timedelta

from arena.lookahead import (LookAheadError, assert_no_lookahead, decision_cutoff, to_utc,
                             visible_items, visible_prices)
from arena import config
from arena.learning.lessons import top_lessons
from arena.sim.executor import load_bars, load_portfolio


def price_summary(rows: list[dict]) -> dict | None:
    closes = [r["close"] for r in rows if r.get("close")]
    vols = [r["volume"] or 0 for r in rows]
    if len(closes) < 21:
        return None

    def ret(n):
        return closes[-1] / closes[-1 - n] - 1 if len(closes) > n else None

    daily = [b / a - 1 for a, b in zip(closes[-21:], closes[-20:])]
    mean = sum(daily) / len(daily)
    vol20 = math.sqrt(sum((x - mean) ** 2 for x in daily) / (len(daily) - 1)) * math.sqrt(252)
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    v5, v20 = sum(vols[-5:]) / 5, sum(vols[-20:]) / 20
    return {
        "close": closes[-1], "ret_5d": ret(5), "ret_20d": ret(20), "ret_60d": ret(60),
        "vol_20d": vol20, "vs_ma20": closes[-1] / ma20 - 1,
        "vs_ma50": closes[-1] / ma50 - 1 if ma50 else None,
        "volume_change": v5 / v20 - 1 if v20 else None,
        "as_of": str(rows[-1]["date"])[:10],
    }


def card_trust(db, card_ids: list[int], rated_after: int = 3) -> dict[int, dict]:
    """Per claim: highest trust among its sources, how many sources made it, and whether any of
    them is still unrated (fewer than `rated_after` judged claims, so 0.50 means "unknown")."""
    if not card_ids:
        return {}
    links = db.select("fact_card_sources", {"fact_card_id": ("in", card_ids)},
                      columns="fact_card_id,source_id", order="fact_card_id.asc,article_id.asc")
    sources = {s["id"]: s for s in db.select("sources", columns="id,name,trust_score,claims_evaluated")}
    out: dict[int, dict] = {}
    for link in links:
        s = sources.get(link["source_id"], {})
        trust = s.get("trust_score")
        trust = 0.5 if trust is None else trust   # new sources start neutral
        rec = out.setdefault(link["fact_card_id"], {"trust": 0.0, "sources": [], "unrated": False})
        rec["trust"] = max(rec["trust"], trust)
        rec["sources"].append(s.get("name", "?"))
        if (s.get("claims_evaluated") or 0) < rated_after:
            rec["unrated"] = True
    return out


def build_inputs(db, team_cfg: dict, team_id: int, run_date: date, tickers: list[str]) -> dict:
    cutoff = decision_cutoff(run_date)
    bars = load_bars(db, tickers, run_date - timedelta(days=130), run_date)
    prices = {}
    for t in tickers:
        rows = visible_prices(bars.get(t, []), run_date)
        s = price_summary(rows)
        if s:
            prices[t] = s

    since = cutoff - timedelta(days=team_cfg.get("fact_card_lookback_days", 7))
    filters = {"published_at": ("gte", since)}
    if team_cfg.get("mode") == "live":
        filters["practice"] = False   # placeholder cards from practice mode never reach real teams
    cards = db.select("fact_cards", filters, order="published_at.desc,id.desc")
    cards = visible_items(cards, cutoff)
    info = card_trust(db, [c["id"] for c in cards])
    min_trust = team_cfg.get("min_source_trust", 0.0)
    shown = []
    for c in cards:
        rec = info.get(c["id"], {"trust": 0.5, "sources": [], "unrated": True})
        # The trust filter only hides sources that EARNED a low score. A source nobody has judged
        # yet sits at 0.50 by default, which would otherwise hide all news from strict teams.
        if rec["trust"] < min_trust and not rec.get("unrated"):
            continue
        shown.append({"id": c["id"], "ticker": c["ticker"], "claim": c["claim"],
                      "direction": c["direction"], "type": c["claim_type"],
                      "horizon_days": c["horizon_days"], "confidence": c["confidence"],
                      "trust": round(rec["trust"], 2), "sources": len(rec["sources"]),
                      "unrated": bool(rec.get("unrated")),
                      "corroborated": bool(c.get("corroborated")),
                      "published_at": c["published_at"]})
        if len(shown) >= team_cfg.get("max_fact_cards_in_prompt", 40):
            break

    lessons = top_lessons(db, team_id, bool(team_cfg.get("shared_lessons")), cutoff,
                          team_cfg.get("lessons_in_prompt", config.load("learning")["lessons_in_prompt"]))
    inputs = {"date": run_date, "cutoff": cutoff, "prices": prices, "fact_cards": shown,
              "portfolio": load_portfolio(db, team_id), "lessons": lessons}
    check_inputs(inputs)
    return inputs


def check_inputs(inputs: dict) -> None:
    """Hard stop if anything from after the decision time slipped into an agent's inputs."""
    assert_no_lookahead(inputs["fact_cards"], inputs["cutoff"])
    for t, s in inputs["prices"].items():
        if date.fromisoformat(s["as_of"]) > inputs["date"]:
            raise LookAheadError(f"{t} price from {s['as_of']} used for a {inputs['date']} decision")
    for lesson in inputs["lessons"]:
        if to_utc(lesson["created_at"]) >= inputs["cutoff"]:
            raise LookAheadError("lesson written after the decision time")
