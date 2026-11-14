"""One team's daily decision: Analyst -> Bull -> Bear -> Judge -> risk engine -> order."""
import json
from datetime import date

from pydantic import BaseModel, Field, ValidationError

from arena import config
from arena.agents import prompts
from arena.agents.inputs import build_inputs
from arena.lookahead import LookAheadError
from arena.llm.budget import AIUnavailable, BudgetExceeded
from arena.risk.engine import RiskResult, evaluate, stop_loss_sells
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


def validate_analyst(k: int, universe: set[str], card_ids: set[int], held: set[str] | None = None,
                     focus: set[str] | None = None):
    held = held or set()

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
            if c.action in ("reduce", "sell") and c.ticker not in held:
                continue   # can't sell what the team doesn't own (long-only): skip the debate
            if c.action == "add" and c.ticker not in held:
                c.action = "buy"
            if c.action in ("buy", "add") and focus is not None and c.ticker not in focus:
                continue   # outside the team's focus: it may only sell what it already holds there
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
                     f"{_trust_label(c)}, {c['sources']} source(s){', CORROBORATED' if c['corroborated'] else ''}: {c['claim']}" for c in sel)


def _trust_label(card: dict) -> str:
    return "trust unrated" if card.get("unrated") else f"trust {card['trust']:.2f}"


# ---------- practice-mode stand-ins ----------
def mock_analyst(inputs, k, allowed, cfg=None):
    """Practice stand-in: a simple rule that follows the team's style, so teams differ."""
    cfg = cfg or {}
    pf, prices = inputs["portfolio"], inputs["prices"]
    # Long-term teams rank by 60-day trend, news-heavy teams by the fresh 5-day move, others by 20-day.
    key = "ret_60d" if cfg.get("holding_style") == "long" else "ret_5d" if cfg.get("news_weight", 0) >= 0.6 else "ret_20d"
    ranked = sorted((t for t in allowed if prices[t].get(key) is not None),
                    key=lambda t: prices[t][key], reverse=True)
    cands = []
    for t, p in pf.positions.items():
        if prices.get(t, {}).get("ret_5d") is not None and prices[t]["ret_5d"] < -0.03:
            cands.append({"ticker": t, "action": "reduce", "report": f"[practice] {config.stock_name(t)} fell {prices[t]['ret_5d']:.1%} in 5 days.", "fact_card_ids": []})
    for t in ranked:
        if len(cands) >= k:
            break
        if t not in pf.positions:
            cards = [c["id"] for c in inputs["fact_cards"] if c["ticker"] == t][:2]
            label = {"ret_60d": "60-day", "ret_5d": "5-day", "ret_20d": "20-day"}[key]
            cands.append({"ticker": t, "action": "buy", "report": f"[practice] {config.stock_name(t)} has one of the strongest {label} moves ({prices[t][key]:+.1%}).", "fact_card_ids": cards})
    return {"market_summary": "[practice] Placeholder analysis; no real AI was used.", "candidates": cands[:k]}


def mock_verdict(c: Candidate):
    sell = c.action in ("reduce", "sell")
    # Vary conviction a little so each team's minimum-conviction setting actually matters.
    conviction = 0.55 + (sum(map(ord, c.ticker)) % 5) * 0.05
    return {"ticker": c.ticker, "action": "sell" if sell else "buy", "conviction": round(conviction, 2),
            "target_weight": 0.0 if sell else 0.10, "reasoning": "[practice] Placeholder verdict.",
            "key_evidence": [f"#{i}" for i in c.fact_card_ids] or ["20-day trend"],
            "main_risk": "[practice] Trend may reverse."}


# ---------- the daily process ----------
def quant_text(quant: dict | None, only: set[str] | None = None) -> str:
    if not quant or not quant.get("scores"):
        return "Not available."
    ev = quant.get("evaluation") or {}
    ic = (ev.get("ic_monthly") or {}).get("mean")
    lines = [f"Predicted 20-day return vs SPY from price patterns only (rank 1 = most favourable). "
             f"Historical skill is weak (walk-forward IC {ic:+.3f}); use as a minor hint." if ic is not None else
             "Predicted 20-day return vs SPY from price patterns only (rank 1 = most favourable). Use as a minor hint."]
    for t, s in sorted(quant["scores"].items(), key=lambda kv: kv[1]["rank"]):
        if only is not None and t not in only:
            continue
        lines.append(f"{s['rank']:>2}. {t} {s['pred'] * 100:+.2f}%")
    return "\n".join(lines)


def run_team(db, llm, team: dict, run_date: date, stocks: list[str], benchmark: str,
             risk_cfg: dict, run_id=None, quant: dict | None = None) -> dict:
    cfg = team["config"]
    tid = team["id"]
    summary = {"decisions": 0, "orders": 0, "skipped": None}
    if db.select("decisions", {"team_id": tid, "date": run_date}, columns="id", limit=1):
        summary["skipped"] = "already decided today"
        return summary

    inputs = build_inputs(db, cfg, tid, run_date, stocks + [benchmark])
    if quant and quant["as_of"] > run_date.isoformat():
        raise LookAheadError(f"quant scores from {quant['as_of']} used for a {run_date} decision")
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
    models = cfg.get("models") or {}   # this team's model per role (e.g. a stronger judge)
    card_ids = {c["id"] for c in inputs["fact_cards"]}
    # The team's focus: it may buy only these; stocks it already holds stay sellable.
    focus = config.focus_tickers(cfg)
    allowed = sorted(t for t in prices if t != benchmark and (t in focus or t in pf.positions))
    focus_note = ("" if focus >= set(prices) - {benchmark} else
                  f"Team focus: you may BUY only {', '.join(sorted(focus))}. Stocks already held outside "
                  "the focus may only be reduced or sold.\n")
    style = "short-term (days to weeks)" if cfg.get("holding_style") == "short" else "long-term (weeks to months)"
    analyst_sys = prompts.ANALYST.format(k=k, style=style, news_weight=cfg.get("news_weight", 0.5))
    judge_sys = prompts.JUDGE.format(style=style, news_weight=cfg.get("news_weight", 0.5))
    shown_prices = {t: s for t, s in prices.items() if t in allowed or t == benchmark}
    shown_cards = [c for c in inputs["fact_cards"] if c["ticker"] in allowed or c["ticker"] is None]
    analyst_prompt = (f"Decision date: {run_date} (after the close).\nAllowed tickers: {', '.join(allowed)}\n{focus_note}\n"
                      f"PORTFOLIO\n{portfolio_text(pf, closes)}\n\nPRICES\n{prices_text(shown_prices)}\n\n"
                      f"FACT CARDS\n{cards_text(shown_cards)}\n\nQUANT MODEL\n{quant_text(quant, set(allowed))}\n\nLESSONS\n"
                      + ("\n".join(f"- {l['text']}" for l in inputs["lessons"]) or "None yet."))
    try:
        result = llm.json("analyst", analyst_sys, analyst_prompt, ANALYST_SCHEMA,
                          validate_analyst(k, set(allowed), card_ids, set(pf.positions), focus), max_tokens=6000,
                          mock=lambda: mock_analyst(inputs, k, allowed, cfg), model_key=models.get("analyst"))
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
            bull = llm.text("bull", prompts.BULL, case, max_tokens=2000, model_key=models.get("bull"),
                            mock=lambda c=c: f"[practice] Case for {c.action} {config.stock_name(c.ticker)}: trend and cited cards.")
            bear = llm.text("bear", prompts.BEAR, case, max_tokens=2000, model_key=models.get("bear"),
                            mock=lambda c=c: f"[practice] Case against {c.action} {config.stock_name(c.ticker)}: evidence is thin.")
            judge_prompt = case + f"\n\nBULL\n{bull or '(missing)'}\n\nBEAR\n{bear or '(missing)'}\n\nReturn the verdict for {c.ticker}."
            verdict = llm.json("judge", judge_sys, judge_prompt, JUDGE_SCHEMA, validate_judge(c.ticker),
                               max_tokens=6000, mock=lambda c=c: mock_verdict(c), model_key=models.get("judge"))
            if verdict is None:
                record(c.ticker, "skipped: invalid judge output", c.report, bull, bear)
                continue
            risk = evaluate(verdict, pf, closes, risk_cfg, cfg.get("min_conviction", 0.6), new_positions)
            if risk.side == "buy" and c.ticker not in focus:   # code enforces the focus, not just the prompt
                risk = RiskResult("vetoed", None, 0, "outside the team's focus sectors")
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
        unavailable = isinstance(e, AIUnavailable)
        record(None, "skipped: AI unavailable" if unavailable else "skipped: AI budget cap reached",
               analyst_report=str(e))
        summary["skipped"] = "ai unavailable" if unavailable else "budget"
    return summary

