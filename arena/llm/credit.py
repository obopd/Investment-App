"""Prepaid-credit countdown: an estimate from logged AI costs (the real balance is in the
Console). Also spots the provider's own "credit balance is too low" refusal."""
from datetime import date, datetime, timedelta, timezone

from arena.market_calendar import is_trading_day

OUT_OF_CREDIT = "credit balance is too low"


def credit_status(db, cfg: dict, today: date, failures: list[dict]) -> dict | None:
    if cfg.get("credit_usd") is None:
        return None
    since = date.fromisoformat(str(cfg["credit_added_on"]))
    start = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    rows = db.select("llm_usage", {"created_at": ("gte", start)}, columns="cost_usd,created_at", order="id.asc")
    spent = sum(float(r["cost_usd"] or 0) for r in rows)
    left = max(float(cfg["credit_usd"]) - spent, 0.0)
    # Average spend per trading day so far -> rough "runs out around" date.
    days_used = sum(1 for i in range((today - since).days + 1) if is_trading_day(since + timedelta(days=i)))
    per_day = spent / days_used if days_used and spent > 0 else None
    runs_out = None
    if per_day:
        n, d = left / per_day, today
        while n > 0:
            d += timedelta(days=1)
            if is_trading_day(d):
                n -= 1
        runs_out = d.isoformat()
    refused = any(OUT_OF_CREDIT in (f.get("error") or "") for f in failures)
    return {"credit_usd": float(cfg["credit_usd"]), "since": since.isoformat(), "spent_usd": round(spent, 4),
            "left_usd": 0.0 if refused else round(left, 4), "per_trading_day_usd": per_day and round(per_day, 4),
            "runs_out_around": None if refused else runs_out, "out_of_credit": refused,
            "low": refused or left <= float(cfg.get("low_credit_warning_usd", 2.0))}
