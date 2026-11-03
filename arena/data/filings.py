"""SEC EDGAR filings (8-K, 10-Q, 10-K), stored as articles with kind='filing'.

SEC rules: a User-Agent with contact info and at most 10 requests/second.
https://www.sec.gov/os/accessing-edgar-data
"""
import os
import time
from datetime import date, datetime, timedelta, timezone

from arena.data.common import get_or_create_sources, safe_published_at, store_articles
from arena.http import TIMEOUT

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

# Plain-language names for common 8-K items, so later AI steps know what a filing is about.
ITEMS_8K = {
    "1.01": "material agreement", "1.02": "agreement terminated", "1.05": "cybersecurity incident",
    "2.01": "acquisition or sale of assets", "2.02": "results of operations (earnings)",
    "2.03": "new debt obligation", "2.05": "restructuring costs", "2.06": "impairment",
    "3.01": "delisting notice", "4.01": "auditor change", "4.02": "prior financials unreliable",
    "5.02": "director or officer change", "5.03": "charter or bylaws amended",
    "5.07": "shareholder vote results", "7.01": "Regulation FD disclosure", "8.01": "other events",
    "9.01": "financial statements and exhibits",
}
FORM_NAMES = {"10-K": "annual report", "10-Q": "quarterly report", "8-K": "current report"}


class FilingsError(RuntimeError):
    pass


def require_user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in ua:
        raise FilingsError("SEC_USER_AGENT must be set to a name and contact email (GitHub Secrets).")
    return ua


def load_cik_map(session) -> dict[str, int]:
    r = session.get(TICKERS_URL, timeout=TIMEOUT)
    if r.status_code != 200:
        raise FilingsError(f"EDGAR ticker map: HTTP {r.status_code}")
    return {row["ticker"].upper(): int(row["cik_str"]) for row in r.json().values()}


def _parse_acceptance(s: str) -> datetime | None:
    # Format "2026-11-02T21:30:23.000Z" (UTC).
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def parse_submissions(data: dict, ticker: str, cik: int, forms: list[str], since: date,
                      collected_at: datetime) -> list[dict]:
    recent = data.get("filings", {}).get("recent", {})
    name = data.get("name") or ticker
    out = []
    for i, form in enumerate(recent.get("form", [])):
        if form not in forms or recent["filingDate"][i] < since.isoformat():
            continue
        acc = recent["accessionNumber"][i].replace("-", "")
        doc = recent["primaryDocument"][i]
        items = [x.strip() for x in (recent.get("items", [""] * (i + 1))[i] or "").split(",") if x.strip()]
        about = ", ".join(ITEMS_8K.get(x, f"item {x}") for x in items if x != "9.01")
        title = f"{name} {form} {FORM_NAMES.get(form, 'filing')}" + (f": {about}" if about else "")
        out.append({
            "url": ARCHIVE_URL.format(cik=cik, acc=acc, doc=doc),
            "kind": "filing",
            "feed": "SEC EDGAR",
            "title": title[:500],
            "snippet": recent.get("primaryDocDescription", [""] * (i + 1))[i] or None,
            "published_at": safe_published_at(_parse_acceptance(recent["acceptanceDateTime"][i]), collected_at),
            "collected_at": collected_at,
            "tickers": [ticker],
        })
    return out


def collect_filings(db, session, tickers: list[str], collected_at: datetime, forms: list[str],
                    lookback_days: int, lookback_days_first_run: int, delay: float = 0.2) -> dict:
    summary = {"new_filings": 0, "errors": {}}
    any_stored = db.select("articles", {"kind": "filing"}, columns="id", limit=1)
    days = lookback_days if any_stored else lookback_days_first_run
    since = collected_at.date() - timedelta(days=days)

    cik_map = load_cik_map(session)
    items = []
    for ticker in tickers:
        cik = cik_map.get(ticker.upper())
        if cik is None:
            summary["errors"][ticker] = "no CIK (ETFs like SPY don't file 8-K/10-Q)"
            continue
        try:
            r = session.get(SUBMISSIONS_URL.format(cik=cik), timeout=TIMEOUT)
            if r.status_code != 200:
                raise FilingsError(f"HTTP {r.status_code}")
            items += parse_submissions(r.json(), ticker, cik, forms, since, collected_at)
        except Exception as e:
            summary["errors"][ticker] = str(e)[:200]
        time.sleep(delay)

    if items:
        sid = get_or_create_sources(db, {"sec.gov": "SEC EDGAR"})["sec.gov"]
        for it in items:
            it["source_id"] = sid
    summary["new_filings"] = store_articles(db, items)
    return summary
