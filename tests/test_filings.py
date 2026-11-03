import json
from datetime import date, datetime, timezone

import pytest

from arena.data.filings import FilingsError, collect_filings, parse_submissions, require_user_agent
from arena.db.client import MemoryDB
from fakes import FakeResponse, FakeSession

NOW = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
SUBMISSIONS = {
    "name": "Apple Inc.",
    "filings": {"recent": {
        "form": ["8-K", "4", "10-K", "8-K"],
        "filingDate": ["2026-11-02", "2026-10-29", "2026-10-30", "2025-01-01"],
        "acceptanceDateTime": ["2026-11-02T21:30:23.000Z", "2026-10-29T22:30:14.000Z",
                               "2026-10-30T20:01:00.000Z", "2025-01-01T12:00:00.000Z"],
        "accessionNumber": ["0000320193-26-000024", "x", "0000320193-26-000020", "old"],
        "primaryDocument": ["aapl-20261102.htm", "f4.xml", "aapl-10k.htm", "old.htm"],
        "items": ["2.02,9.01", "", "", "8.01"],
        "primaryDocDescription": ["8-K", "", "10-K", ""],
    }},
}


def test_parse_submissions_filters_forms_and_dates():
    rows = parse_submissions(SUBMISSIONS, "AAPL", 320193, ["8-K", "10-Q", "10-K"], date(2026, 10, 20), NOW)
    assert [r["title"] for r in rows] == [
        "Apple Inc. 8-K current report: results of operations (earnings)",
        "Apple Inc. 10-K annual report",
    ]
    assert rows[0]["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000024/aapl-20261102.htm"
    assert rows[0]["published_at"] == datetime(2026, 11, 2, 21, 30, 23, tzinfo=timezone.utc)
    assert rows[0]["kind"] == "filing" and rows[0]["tickers"] == ["AAPL"]


def test_collect_filings_end_to_end_and_rerun():
    sess = FakeSession({
        "https://www.sec.gov/files/company_tickers.json":
            FakeResponse(200, json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})),
        "https://data.sec.gov/submissions/CIK0000320193.json": FakeResponse(200, json.dumps(SUBMISSIONS)),
    })
    db = MemoryDB()
    s = collect_filings(db, sess, ["AAPL", "ZZZZ"], NOW, ["8-K", "10-K"], 10, 30, delay=0)
    assert s["new_filings"] == 2 and "ZZZZ" in s["errors"]
    assert collect_filings(db, sess, ["AAPL"], NOW, ["8-K", "10-K"], 10, 30, delay=0)["new_filings"] == 0
    assert db.select("sources")[0]["name"] == "SEC EDGAR"


def test_sec_requires_contact_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    with pytest.raises(FilingsError):
        require_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "AI-Trading-Arena someone@example.com")
    assert require_user_agent().endswith("@example.com")
