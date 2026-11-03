"""Post-mortem after every closed trade: what happened, what was right or wrong, what was missed,
and ONE concrete reusable lesson."""
import re
from datetime import timedelta

from pydantic import BaseModel, Field, ValidationError

from arena.learning.lessons import add_lesson, maintain, score_lessons_for_trade
from arena.llm.budget import BudgetExceeded
from arena.lookahead import decision_cutoff, to_utc

SYSTEM = (
    "You review one closed trade from an experimental paper-trading simulation (imaginary money, "
    "research only). Be specific and honest; judge the decision by the evidence available when it "
    "was made, not by hindsight luck. Give exactly ONE reusable lesson: a single actionable sentence "
    "of the form 'When <specific condition>, <specific action>.' Never generic advice."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "what_happened": {"type": "string"},
        "right_or_wrong": {"type": "string"},
        "what_was_missed": {"type": "string"},
        "lesson": {"type": "string"},
    },
    "required": ["what_happened", "right_or_wrong", "what_was_missed", "lesson"],
    "additionalProperties": False,
}

GENERIC = re.compile(r"\b(do (your|more) research|be (more )?careful|past performance|"
                     r"always be|stay disciplined|manage risk better)\b|diversif\w*", re.I)


class Review(BaseModel):
    what_happened: str = Field(min_length=10, max_length=1200)
    right_or_wrong: str = Field(min_length=5, max_length=1200)
    what_was_missed: str = Field(min_length=5, max_length=1200)
    lesson: str = Field(min_length=25, max_length=240)


def validate_review(data):
    try:
        r = Review(**data)
    except (ValidationError, TypeError) as e:
        raise ValueError(str(e)[:300]) from e
    lesson = " ".join(r.lesson.split())
    if not re.search(r"\b(when|if)\b", lesson, re.I):
        raise ValueError("the lesson must state a condition ('When ..., ...')")
    if GENERIC.search(lesson):
        raise ValueError("the lesson is generic; make it specific to a condition and an action")
    if len(re.findall(r"[.!?](\s|$)", lesson)) > 1:
        raise ValueError("the lesson must be one sentence")
    r.lesson = lesson
    return r


def mock_review(trade: dict) -> dict:
    beat = trade.get("benchmark_return") is not None and trade["pnl_pct"] > trade["benchmark_return"]
    if trade["pnl_pct"] < -0.08:
        lesson = "[practice] When a position falls more than 8% within two weeks of entry, review it before the stop-loss forces a sale."
    elif beat:
        lesson = "[practice] When a strong 20-day trend is confirmed by rising volume, give the position at least two weeks."
    else:
        lesson = "[practice] When a stock's 5-day return turns negative right after entry, wait for the trend to resume before adding."
    return {"what_happened": f"[practice] {trade['ticker']} returned {trade['pnl_pct']:+.1%} over {trade['holding_days']} days.",
            "right_or_wrong": "[practice] Placeholder review; no real AI was used.",
            "what_was_missed": "[practice] Nothing assessed in practice mode.",
            "lesson": lesson}


def _pct(x):
    return "n/a" if x is None else f"{x:+.1%}"


def _prompt(db, trade: dict, live: bool) -> str:
    entry = db.select("decisions", {"id": trade["entry_decision_id"]}) if trade.get("entry_decision_id") else []
    exit_ = db.select("decisions", {"id": trade["exit_decision_id"]}) if trade.get("exit_decision_id") else []
    start = decision_cutoff(_date(trade["entry_date"])) - timedelta(days=1)
    end = decision_cutoff(_date(trade["exit_date"]))
    filters = {"ticker": trade["ticker"], "published_at": ("gte", start)}
    if live:
        filters["practice"] = False
    cards = [c for c in db.select("fact_cards", filters, order="published_at.asc,id.asc",
                                  columns="id,claim,direction,claim_type,published_at")
             if c["published_at"] and to_utc(c["published_at"]) < end][:15]
    lines = [f"Trade: {trade['ticker']}, {trade['shares']} shares, bought {trade['entry_date']} at {trade['entry_price']:.2f}, "
             f"sold {trade['exit_date']} at {trade['exit_price']:.2f} ({trade['holding_days']} days).",
             f"Result: {_pct(trade['pnl_pct'])} vs SPY {_pct(trade.get('benchmark_return'))} over the same period."]
    if entry:
        e = entry[0]
        v = e.get("judge_verdict") or {}
        lines += ["", "OPENING DECISION", f"Analyst: {e.get('analyst_report') or '-'}",
                  f"Judge: {v.get('reasoning', '-')} (conviction {v.get('conviction', '-')})",
                  f"Key evidence: {', '.join(v.get('key_evidence', [])) or '-'}", f"Main risk named: {v.get('main_risk', '-')}"]
    if exit_:
        lines += ["", f"EXIT: {exit_[0].get('final_action')} - {exit_[0].get('analyst_report') or ''}"]
    lines += ["", "FACT CARDS DURING THE HOLDING PERIOD"]
    lines += [f"#{c['id']} {c['direction']}, {c['claim_type']}: {c['claim']}" for c in cards] or ["None."]
    return "\n".join(lines)


def _date(v):
    from datetime import date
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def write_post_mortems(db, llm, teams: list[dict], cfg: dict, now) -> dict:
    summary = {"written": 0, "lessons_added": 0, "lessons_merged": 0, "skipped_budget": False}
    done = {p["closed_trade_id"] for p in db.select("post_mortems", columns="closed_trade_id", order="closed_trade_id.asc")}
    budget_left = cfg.get("max_post_mortems_per_run", 10)
    for team in teams:
        shared = bool(team["config"].get("shared_lessons"))
        trades = [t for t in db.select("closed_trades", {"team_id": team["id"]}, order="id.asc") if t["id"] not in done]
        for t in trades:
            if budget_left <= 0 or not llm.available:
                break
            try:
                review = llm.json("post_mortem", SYSTEM, _prompt(db, t, team["config"].get("mode") == "live"),
                                  SCHEMA, validate_review,
                                  max_tokens=3000, mock=lambda t=t: mock_review(t))
            except BudgetExceeded:
                summary["skipped_budget"] = True
                return summary
            budget_left -= 1
            if review is None:
                continue  # invalid twice: logged by the client; retried on a later run
            pm = db.insert("post_mortems", [{
                "closed_trade_id": t["id"], "team_id": team["id"], "what_happened": review.what_happened,
                "right_or_wrong": review.right_or_wrong, "what_was_missed": review.what_was_missed,
                "lesson_candidate": review.lesson, "created_at": now}], returning=True)[0]
            # Scoring happens exactly once per closed trade: together with its post-mortem.
            score_lessons_for_trade(db, t)
            res = add_lesson(db, team["id"], shared, review.lesson, pm["id"], cfg, now)
            summary["lessons_added" if "added" in res else "lessons_merged"] += 1
            summary["written"] += 1
        maintain(db, team["id"], shared, cfg)
    return summary
