"""No-look-ahead rule.

A decision made after day D's close may only use information published before D's close
(16:00 New York time). Everything that feeds an agent must pass through these helpers.
"""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)


class LookAheadError(AssertionError):
    """Raised when data published after the decision time reaches an agent."""


def decision_cutoff(d: date) -> datetime:
    """Day D's market close as an aware UTC datetime (handles daylight saving)."""
    return datetime.combine(d, MARKET_CLOSE, NEW_YORK).astimezone(timezone.utc)


def to_utc(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return dt.astimezone(timezone.utc)


def visible_items(items, cutoff: datetime, key: str = "published_at") -> list:
    """Items published strictly before the cutoff."""
    return [it for it in items if to_utc(it[key]) < cutoff]


def visible_prices(rows, d: date, key: str = "date") -> list:
    """Daily bars up to and including D (D's close is known at decision time)."""
    return [r for r in rows if _as_date(r[key]) <= d]


def assert_no_lookahead(items, cutoff: datetime, key: str = "published_at") -> None:
    late = [it for it in items if to_utc(it[key]) >= cutoff]
    if late:
        raise LookAheadError(f"{len(late)} item(s) published at/after {cutoff.isoformat()}: {late[0]}")


def _as_date(v) -> date:
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v)[:10])
