from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from arena import config
from arena.db.client import MemoryDB
from arena.llm.budget import BudgetExceeded, BudgetGuard
from arena.llm.client import LLMClient, make_llm
from arena.llm.providers import AnthropicProvider

CFG = config.load("models")


class ScriptedProvider:
    """Returns queued replies; records what it was asked."""
    name = "anthropic"

    def __init__(self, replies, tokens=(1000, 200), stop="end_turn"):
        self.replies, self.tokens, self.stop, self.calls = list(replies), tokens, stop, []

    def complete(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0), *self.tokens, self.stop


def client(provider, guard=None, db=None):
    return LLMClient(db or MemoryDB(), 1, CFG, guard or BudgetGuard(5, 1), {"anthropic": provider})


def positive(data):
    if data["x"] <= 0:
        raise ValueError("x must be positive")
    return data["x"]


def test_cost_is_logged_per_call_from_config_prices():
    db = MemoryDB()
    llm = client(ScriptedProvider(['{"x": 1}']), db=db)
    assert llm.json("judge", "s", "p", {}, positive) == 1
    row = db.select("llm_usage")[0]
    strong = CFG["models"]["strong"]
    assert row["model"] == strong["model"] and row["role"] == "judge"
    assert row["cost_usd"] == pytest.approx(1000 / 1e6 * strong["input_per_mtok"] + 200 / 1e6 * strong["output_per_mtok"])


def test_invalid_output_is_retried_once_then_skipped_and_logged():
    p = ScriptedProvider(['{"x": -1}', '```json\n{"x": 2}\n```'])
    llm = client(p)
    assert llm.json("analyst", "s", "p", {}, positive) == 2
    assert "previous reply was invalid" in p.calls[1]["prompt"]

    llm = client(ScriptedProvider(["not json", '{"x": 0}']))
    assert llm.json("analyst", "s", "p", {}, positive) is None
    assert "invalid output twice" in llm.failures[0]["error"]


def test_refusal_counts_as_invalid():
    llm = client(ScriptedProvider(['{"x": 1}', '{"x": 1}'], stop="refusal"))
    assert llm.json("judge", "s", "p", {}, positive) is None


def test_budget_guard_blocks_calls_before_spending():
    guard = BudgetGuard(monthly_cap=0.001, per_run_cap=1)
    p = ScriptedProvider(['{"x": 1}'])
    with pytest.raises(BudgetExceeded):
        client(p, guard).json("judge", "s", "p", {}, positive, max_tokens=2000)
    assert p.calls == [] and guard.skipped_calls == 1


def test_month_spend_is_read_from_llm_usage():
    db = MemoryDB()
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    db.insert("llm_usage", [{"cost_usd": 3.0, "created_at": "2026-11-02T00:00:00+00:00"},
                            {"cost_usd": 9.0, "created_at": "2026-10-30T00:00:00+00:00"}])
    g = BudgetGuard.from_db(db, {"monthly_cap_usd": 5, "per_run_cap_usd": 1}, now)
    assert g.month_spent == 3.0 and not g.exhausted


def test_practice_mode_costs_nothing_and_live_without_key_is_unavailable(monkeypatch):
    db = MemoryDB()
    llm = make_llm(db, 1, {**CFG, "mode": "practice"}, BudgetGuard(5, 1), dry_run=False)
    assert llm.json("judge", "s", "p", {}, positive, mock=lambda: {"x": 3}) == 3
    assert db.select("llm_usage")[0]["cost_usd"] == 0
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert make_llm(db, 1, {**CFG, "mode": "live"}, BudgetGuard(5, 1), dry_run=False).available is False


def test_anthropic_request_uses_structured_output_and_effort():
    sent = {}

    class Messages:
        def create(self, **kw):
            sent.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""),
                                            SimpleNamespace(type="text", text='{"ok": true}')],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5), stop_reason="end_turn")
    p = AnthropicProvider("k", client=SimpleNamespace(messages=Messages()))
    text, tin, tout, stop = p.complete(model="m", system="s", prompt="p", max_tokens=100,
                                       schema={"type": "object"}, effort="low")
    assert text == '{"ok": true}' and (tin, tout, stop) == (10, 5, "end_turn")
    assert sent["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert sent["messages"] == [{"role": "user", "content": "p"}] and sent["system"] == "s"


def test_provider_error_stops_ai_for_the_run_and_nothing_is_marked_done():
    from arena.facts.extract import extract_fact_cards
    from arena.llm.budget import AIUnavailable
    from arena.llm.providers import ProviderError

    class NoCredit:
        name = "anthropic"
        calls = 0

        def complete(self, **kw):
            NoCredit.calls += 1
            raise ProviderError("BadRequestError: Your credit balance is too low")
    db = MemoryDB()
    now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    db.insert("sources", [{"id": 1, "name": "W", "domain": "w.com"}])
    db.insert("articles", [{"id": i, "source_id": 1, "url": f"u{i}", "title": f"Apple news {i}", "tickers": ["AAPL"],
                            "published_at": "2026-11-04T10:00:00+00:00"} for i in range(25)])
    llm = client(NoCredit(), db=db)
    s = extract_fact_cards(db, llm, {"AAPL"}, config.load("facts"), now)
    assert s["skipped_budget"] and s["articles_processed"] == 0
    assert NoCredit.calls == 1                       # stopped after the first failure
    assert not any(a.get("facts_extracted_at") for a in db.select("articles"))   # retried next run
    assert llm.available is False
    with pytest.raises(AIUnavailable):
        llm.json("judge", "s", "p", {}, positive)
