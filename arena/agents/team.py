"""One team's daily decision: Analyst -> Bull -> Bear -> Judge -> risk engine -> order."""
import json
from datetime import date

from pydantic import BaseModel, Field, ValidationError

from arena.agents import prompts
from arena.agents.inputs import build_inputs
from arena.llm.budget import BudgetExceeded
from arena.risk.engine import evaluate, stop_loss_sells
from arena.sim.executor import next_order_date

# ---------- schemas (structured output + validation) ----------
ANALYST_SCHEMA = {
    "type": "object",
    "properties": {
        "market_summary": {"type": "string"},
        "candidates": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "action": {"type": "string", "enum": ["buy", "add", "reduce", "sell"]},
                "report": {"type": "string"},
                "fact_card_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["ticker", "action", "report", "fact_card_ids"],
            "additionalProperties": False}},
    },
    "required": ["market_summary", "candidates"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "conviction": {"type": "number"},
        "target_weight": {"type": "number"},
        "reasoning": {"type": "string"},
        "key_evidence": {"type": "array", "items": {"type": "string"}},
        "main_risk": {"type": "string"},
    },
    "required": ["ticker", "action", "conviction", "target_weight", "reasoning", "key_evidence", "main_risk"],
    "additionalProperties": False,
}


class Candidate(BaseModel):
    ticker: str
    action: str
    report: str = Field(min_length=1, max_length=2000)
    fact_card_ids: list[int]


class Verdict(BaseModel):
    ticker: str
    action: str
    conviction: float = Field(ge=0, le=1)
    target_weight: float = Field(ge=0, le=1)
    reasoning: str = Field(max_length=1500)
    key_evidence: list[str]
    main_risk: str = Field(max_length=600)


def validate_analyst(k: int, universe: set[str], card_ids: set[int]):
    def v(data):
        try:
            cands = [Candidate(**c) for c in data["candidates"]]
        except (ValidationError, TypeError, KeyError) as e:
            raise ValueError(str(e)[:300]) from e
        seen, out = set(), []
        for c in cands:
            c.ticker = c.ticker.upper().lstrip("$")
            if c.ticker not in universe:
                raise ValueError(f"{c.ticker} is not in the allowed ticker list")
            if c.action not in ("buy", "add", "reduce", "sell") or c.ticker in seen:
                continue
            seen.add(c.ticker)
            c.fact_card_ids = [i for i in c.fact_card_ids if i in card_ids]  # drop invented ids
            out.append(c)
        return str(data.get("market_summary", ""))[:500], out[:k]
    return v


def validate_judge(ticker: str):
    def v(data):
        try:
            verdict = Verdict(**data)
        except (ValidationError, TypeError) as e:
            raise ValueError(str(e)[:300]) from e
        if verdict.ticker.upper() != ticker:
            raise ValueError(f"verdict must be about {ticker}")
        if verdict.action not in ("buy", "sell", "hold"):
            raise ValueError("action must be buy, sell or hold")
        verdict.ticker = ticker
        return verdict.model_dump()
    return v


# ---------- prompt text ----------
def _pct(x):
    return "n/a" if x is None else f"{x:+.1%}"


def portfolio_text(pf, closes) -> str:
    eq = pf.equity(closes)
    lines = [f"Cash ${pf.cash:,.0f} of equity ${eq:,.0f}" + (" (DRAWDOWN BRAKE ON: reduce only)" if pf.brake_active else "")]
    for t, p in sorted(pf.positions.items()):
        px = closes.get(t, p.avg_cost)
        lines.append(f"{t}: {p.shares} sh, weight {p.shares * px / eq:.1%}, P&L {px / p.avg_cost - 1:+.1%}, since {p.opened_at}")
    return "\n".join(lines) if pf.positions else lines[0] + "\nNo positions."


def prices_text(prices: dict, only: list[str] | None = None) -> str:
    rows = ["ticker | close | 5d | 20d | 60d | vol20 | vs MA20 | vs MA50 | volume chg"]
    for t in only or sorted(prices):
        s = prices.get(t)
        if s:
            rows.append(f"{t} | {s['close']:.2f} | {_pct(s['ret_5d'])} | {_pct(s['ret_20d'])} | {_pct(s['ret_60d'])} | "
                        f"{s['vol_20d']:.0%} | {_pct(s['vs_ma20'])} | {_pct(s['vs_ma50'])} | {_pct(s['volume_change'])}")
    return "\n".join(rows)


def cards_text(cards: list[dict], ticker: str | None = None) -> str:
    sel = [c for c in cards if ticker is None or c["ticker"] in (ticker, None)]
    if not sel:
        return "No fact cards."
    return "\n".join(f"#{c['id']} [{c['ticker'] or 'MACRO'}] {c['direction']}, {c['type']}, {c['horizon_days']}d, "
                     f"trust {c['trust']:.2f}, {c['sources']} source(s){', CORROBORATED' if c['corroborated'] else ''}: {c['claim']}" for c in sel)


# ---------- practice-mode stand-ins ----------
def mock_analyst(inputs, k, allowed):
    pf, prices = inputs["portfolio"], inputs["prices"]
    ranked = sorted((t for t in allowed if prices[t]["ret_20d"] is not None),
                    key=lambda t: prices[t]["ret_20d"], reverse=True)
    cands = []
    for t, p in pf.positions.items():
        if prices.get(t, {}).get("ret_5d") is not None and prices[t]["ret_5d"] < -0.03:
            cands.append({"ticker": t, "action": "reduce", "report": f"[practice] {t} fell {prices[t]['ret_5d']:.1%} in 5 days.", "fact_card_ids": []})
    for t in ranked:
        if len(cands) >= k:
            break
        if t not in pf.positions:
            cards = [c["id"] for c in inputs["fact_cards"] if c["ticker"] == t][:2]
            cands.append({"ticker": t, "action": "buy", "report": f"[practice] {t} has the strongest 20-day trend ({prices[t]['ret_20d']:+.1%}).", "fact_card_ids": cards})
    return {"market_summary": "[practice] Placeholder analysis; no real AI was used.", "candidates": cands[:k]}


def mock_verdict(c: Candidate):
    sell = c.action in ("reduce", "sell")
    return {"ticker": c.ticker, "action": "sell" if sell else "buy", "conviction": 0.7,
            "target_weight": 0.0 if sell else 0.10, "reasoning": "[practice] Placeholder verdict.",
            "key_evidence": [f"#{i}" for i in c.fact_card_ids] or ["20-day trend"],
            "main_risk": "[practice] Trend may reverse."}


# ---------- the daily process ----------
def run_team(db, llm, team: dict, run_date: date, stocks: list[str], benchmark: str,
             risk_cfg: dict, run_id=None) -> dict:
    cfg = team["config"]
    tid = team["id"]
    summary = {"decisions": 0, "orders": 0, "skipped": None}
    if db.select("decisions", {"team_id": tid, "date": run_date}, columns="id", limit=1):
        summary["skipped"] = "already decided today"
        return summary

    inputs = build_inputs(db, cfg, tid, run_date, stocks + [benchmark])
    pf, prices = inputs["portfolio"], inputs["prices"]
    closes = {t: s["close"] for t, s in prices.items()}
    for_date = next_order_date(run_date)
    pending = {o["ticker"] for o in db.select("orders", {"team_id": tid, "status": "pending"}, columns="ticker")}

    def record(ticker, final_action, analyst_report=None, bull=None, bear=None, verdict=None, risk=None):
        row = db.insert("decisions", [{
            "run_id": run_id, "team_id": tid, "date": run_date, "ticker": ticker,
            "analyst_report": analyst_report, "bull_case": bull, "bear_case": bear,
            "judge_verdict": verdict, "lessons_used": [l["id"] for l in inputs["lessons"]],
            "risk_result": risk, "final_action": final_action}], returning=True)[0]
        summary["decisions"] += 1
        return row["id"]

    def order(decision_id, ticker, side, shares, reason):
        db.insert("orders", [{"team_id": tid, "decision_id": decision_id, "ticker": ticker, "side": side,
                              "shares": shares, "status": "pending", "reason": reason, "for_date": for_date}])
        pending.add(ticker)
        summary["orders"] += 1

    # 1) Stop-losses are code-only and come first.
    for t, n, why in stop_loss_sells(pf, closes, risk_cfg):
        if t in pending:
            continue
        did = record(t, f"sell {n} (stop-loss)", analyst_report="Risk engine: " + why,
                     risk={"status": "approved", "side": "sell", "shares": n, "reason": why})
        order(did, t, "sell", n, why)

    if not llm.available:
        record(None, "no AI: decisions skipped", analyst_report="No AI provider configured (add ANTHROPIC_API_KEY or use practice mode).")
        summary["skipped"] = "no AI provider"
        return summary

    k = cfg.get("max_candidates_per_day", 3)
    card_ids = {c["id"] for c in inputs["fact_cards"]}
    allowed = sorted(t for t in prices if t != benchmark)  # the benchmark itself isn't traded
    analyst_sys = prompts.ANALYST.format(k=k, style="short-term (days to weeks)" if cfg.get("holding_style") == "short" else "long-term (weeks to months)",
                                         news_weight=cfg.get("news_weight", 0.5))
    analyst_prompt = (f"Decision date: {run_date} (after the close).\nAllowed tickers: {', '.join(allowed)}\n\n"
                      f"PORTFOLIO\n{portfolio_text(pf, closes)}\n\nPRICES\n{prices_text(prices)}\n\n"
                      f"FACT CARDS\n{cards_text(inputs['fact_cards'])}\n\nLESSONS\n"
                      + ("\n".join(f"- {l['text']}" for l in inputs["lessons"]) or "None yet."))
    try:
        result = llm.json("analyst", analyst_sys, analyst_prompt, ANALYST_SCHEMA,
                          validate_analyst(k, set(allowed), card_ids), max_tokens=6000,
                          mock=lambda: mock_analyst(inputs, k, allowed))
        if result is None:
            record(None, "skipped: invalid analyst output", analyst_report="The analyst's reply failed validation twice.")
            return summary
        market_summary, candidates = result
        candidates = [c for c in candidates if c.ticker not in pending]
        if not candidates:
            record(None, "no action", analyst_report=market_summary or "No candidates today.")
            return summary

        new_positions = 0
        for c in candidates:
            evidence = cards_text([x for x in inputs["fact_cards"] if x["id"] in c.fact_card_ids] or inputs["fact_cards"], c.ticker)
            case = (f"Decision date: {run_date}. Proposed action: {c.action.upper()} {c.ticker}.\n"
                    f"Analyst report: {c.report}\n\nPRICES\n{prices_text(prices, [c.ticker, 'SPY'])}\n\n"
                    f"EVIDENCE\n{evidence}\n\nPORTFOLIO\n{portfolio_text(pf, closes)}")
            bull = llm.text("bull", prompts.BULL, case, max_tokens=2000,
                            mock=lambda c=c: f"[practice] Case for {c.action} {c.ticker}: trend and cited cards.")
            bear = llm.text("bear", prompts.BEAR, case, max_tokens=2000,
                            mock=lambda c=c: f"[practice] Case against {c.action} {c.ticker}: evidence is thin.")
            judge_prompt = case + f"\n\nBULL\n{bull or '(missing)'}\n\nBEAR\n{bear or '(missing)'}\n\nReturn the verdict for {c.ticker}."
            verdict = llm.json("judge", prompts.JUDGE, judge_prompt, JUDGE_SCHEMA, validate_judge(c.ticker),
                               max_tokens=6000, mock=lambda c=c: mock_verdict(c))
            if verdict is None:
                record(c.ticker, "skipped: invalid judge output", c.report, bull, bear)
                continue
            risk = evaluate(verdict, pf, closes, risk_cfg, cfg.get("min_conviction", 0.6), new_positions)
            if risk.side:
                final = f"{risk.side} {risk.shares}"
            else:
                final = "hold" if risk.status == "hold" else f"vetoed: {risk.reason}"
            did = record(c.ticker, final, c.report, bull, bear, verdict, risk.to_dict())
            if risk.side:
                order(did, c.ticker, risk.side, risk.shares, risk.reason)
                if risk.side == "buy":
                    if c.ticker not in pf.positions:
                        new_positions += 1
                    # Reserve the cash so later buys today are sized against what's left.
                    pf.cash -= risk.shares * closes[c.ticker] * (1 + risk_cfg["slippage_pct"]) * (1 + risk_cfg["fee_pct"])
    except BudgetExceeded as e:
        record(None, "skipped: AI budget cap reached", analyst_report=str(e))
        summary["skipped"] = "budget"
    return summary

