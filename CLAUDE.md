# AI Trading Arena — Project Specification

## 0. Rules for Claude Code
- Build ONE phase at a time (Section 15). Finish it, run tests, summarize briefly, then STOP and wait for my approval before the next phase.
- I work mainly from a phone. Keep summaries short, tell me exactly what to check, and avoid steps that need a desktop computer.
- Never commit secrets. Use GitHub Secrets and environment variables only. Add a .env.example with empty placeholders.
- Never connect to a real brokerage or place real orders. Imaginary money only.
- Prefer simple, readable code. Comment the "why," not the "what."
- If something is unclear or impossible, say so and propose an alternative instead of guessing.
- Keep a PROGRESS.md file updated: done, next, known issues.

## 1. What the app is
An experiment where several AI "teams" trade imaginary money using only free, public information. Each team is a group of AI agents that research, debate, and decide trades. Teams learn by:
1. Writing post-mortems on closed trades and building a lesson book
2. Learning which information sources are trustworthy by checking whether their claims came true
3. Competing: monthly, the worst team is replaced by a modified copy of the best team (evolution)
A mobile PWA dashboard shows a leaderboard, every trade with the agents' reasoning, the lesson book, source trust rankings, and comparison against a buy-and-hold SPY benchmark.
This is a research project, not a financial product. It never gives investment advice and never trades real money.

## 2. Core principles
- Daily cadence: teams decide once per trading day after US market close. No intraday trading.
- Small universe: about 30 large, liquid US stocks (config/universe.yaml) plus SPY as benchmark.
- No look-ahead: decisions after day D's close use only information published before day D's close. Trades execute at day D+1's open.
- Numbers by code, judgment by AI: all calculations (returns, sizing, risk, metrics) are deterministic code. AI only reads text, reasons, and outputs structured decisions.
- Risk rules are code and have veto power over AI.
- AI teams are evaluated on live paper trading only. Never backtest AI agents on past dates, because AI models already know historical outcomes from training. Historical data is used only for features and the quant model.
- Log everything: every AI call, decision, debate, trade, lesson.
- Hard monthly AI spending cap.

## 3. Tech stack
- Backend/pipeline: Python 3.11+
- Database: Supabase (PostgreSQL) with Row Level Security on every table
- Scheduler: GitHub Actions cron, daily after US market close
- Frontend: PWA (plain HTML/CSS/JS), hosted on GitHub Pages, reading Supabase with a READ-ONLY public key
- AI providers: pluggable, start with Anthropic. Optionally add OpenAI and Google later. Model names live in config/models.yaml, never hardcoded.
- Tests: pytest

Suggested structure:
/config (universe.yaml, models.yaml, teams.yaml, risk.yaml, budget.yaml)
/arena/data (price, news, filings collectors, pluggable providers)
/arena/facts (article -> fact card extraction)
/arena/trust (source trust scoring)
/arena/agents (analyst, bull, bear, judge)
/arena/sim (portfolio simulator, fills, fees, slippage)
/arena/risk (risk rule engine)
/arena/learning (post-mortems, lesson book, evolution)
/arena/quant (Phase 6)
/arena/llm (provider-agnostic AI client, cost tracking, budget guard)
/arena/db (Supabase client, migrations)
/arena/pipeline.py (daily entry point)
/dashboard (PWA)
/tests
/.github/workflows
CLAUDE.md, PROGRESS.md, .env.example

## 4. Data sources (free and public only)
All collectors are pluggable because free sources change.
- Daily prices (OHLCV): a free source. Check its terms of use and note them in PROGRESS.md. Store full available daily history for the universe.
- News: public RSS feeds from financial news sites and company press-release pages. Store title, snippet, URL, source, published_at, collected_at. Respect robots.txt and terms. Never bypass paywalls.
- Filings: SEC EDGAR (8-K, 10-Q, 10-K). Use a descriptive User-Agent with contact info and respect rate limits.
- published_at is what the no-look-ahead rule uses.

## 5. Database tables
- stocks: ticker, name, sector, active
- prices_daily: ticker, date, open, high, low, close, adj_close, volume
- articles: id, source_id, url (unique), title, snippet, published_at, collected_at, tickers
- sources: id, name, domain, trust_alpha, trust_beta, trust_score, claims_evaluated
- fact_cards: id, article_id, ticker, claim, direction (positive/negative/neutral), claim_type (earnings, guidance, product, legal, macro, rumor, other), horizon_days, confidence, created_at
- claim_evaluations: fact_card_id, evaluated_at, excess_return, outcome (correct/incorrect/unclear)
- teams: id, name, generation, parent_team_id, config JSON, status, created_at
- portfolios: team_id, cash
- positions: team_id, ticker, shares, avg_cost, opened_at
- orders: id, team_id, decision_id, ticker, side, shares, status, for_date
- trades: id, order_id, team_id, ticker, side, shares, fill_price, fees, slippage, executed_at
- decisions: id, team_id, date, ticker, analyst_report, bull_case, bear_case, judge_verdict JSON, lessons_used, risk_result, final_action
- closed_trades: id, team_id, ticker, entry/exit dates and prices, pnl, pnl_pct, holding_days
- post_mortems: closed_trade_id, team_id, what_happened, what_was_missed, lesson_candidate
- lessons: id, team_id (null = shared), text, times_used, times_helpful, score, active
- equity_daily: team_id, date, cash, positions_value, total_equity
- benchmark_daily: date, equity
- llm_usage: run_id, provider, model, role, input_tokens, output_tokens, cost_usd
- runs: id, started_at, finished_at, status, error
Security: dashboard key is read-only on display tables. Only the pipeline writes, using a service key in GitHub Secrets.

## 6. Daily pipeline (arena/pipeline.py)
Runs each trading day after close, skips weekends and market holidays, and is safe to re-run.
1. Budget check. If the monthly AI cap is reached, run only non-AI steps and log a warning.
2. Execute yesterday's pending orders at today's open, with fees and slippage.
3. Collect today's prices, new articles, new filings.
4. Extract fact cards from new articles with a cheap AI model, strict JSON. Deduplicate near-identical claims but record every source that made them.
5. Evaluate matured claims and update source trust (Section 9).
6. Update equity for all teams and the benchmark.
7. Detect closed trades and write post-mortems (Section 10).
8. Run each active team's decision process (Section 7) and create orders for tomorrow's open.
9. Log costs and mark the run finished.
Flags: --dry-run (mock AI responses, zero cost) and --date (testing).

## 7. Team decision process
Inputs prepared by code (no look-ahead):
- Price summary per ticker: 5/20/60-day returns, volatility, distance from moving averages, volume change
- Recent fact cards, each labeled with its source's trust score and corroboration
- Current portfolio and cash
- Top lessons from the lesson book (limited count)
- After Phase 6: quant model score per ticker
Roles:
1. Analyst: picks up to K tickers to act on (buy, add, reduce, sell) and writes a short evidence report citing fact card IDs.
2. Bull: strongest case for the action.
3. Bear: strongest case against, attacking weak or low-trust evidence.
4. Judge: outputs strict JSON:
   {"ticker": "", "action": "buy|sell|hold", "conviction": 0.0, "target_weight": 0.0, "reasoning": "", "key_evidence": [], "main_risk": ""}
5. Risk engine (code): approves, resizes, or vetoes, and saves the reason.
Requirements: validate all AI output against a schema; retry once on invalid output, then skip and log. Where configured, the Judge uses a different model or provider than Bull and Bear. "Hold" is a normal, acceptable outcome.

## 8. Simulator and risk
- Each team starts with $100,000 imaginary money (configurable).
- Long-only. No shorting, leverage, or options.
- Fill at next trading day's open. Fee and slippage per trade (e.g., 0.05% each, configurable). Whole shares only. Orders larger than cash are reduced.
Risk rules (config/risk.yaml), enforced in code:
- Max weight per stock (e.g., 15%)
- Max number of positions
- Minimum cash buffer
- Stop-loss per position (e.g., -12% from entry triggers a sell)
- Max new positions per day
- Drawdown brake: if team equity falls more than X% from peak, it may only reduce positions until partly recovered
Benchmark: same starting money in SPY on day one, held, tracked daily, always shown next to every team.

## 9. Source trust scoring (fake-information filter)
- Each fact card has a direction and a horizon (e.g., 5 or 20 trading days).
- When the horizon passes, compute the ticker's excess return (stock minus SPY).
- Correct if the direction matches beyond a small threshold; incorrect if opposite beyond the threshold; otherwise unclear (no trust change).
- Each source keeps Beta counts: alpha = correct + 1, beta = incorrect + 1, trust_score = alpha / (alpha + beta). New sources start at 0.5.
- Label claims independently repeated by several higher-trust sources as corroborated.
- Mark claims later contradicted by official filings or company statements as incorrect.
- Dashboard note: a price move doesn't prove a claim true or false; trust is a statistical signal that only means something over many claims.

## 10. Learning
Post-mortem after every closed trade:
- Inputs: original decision record, price outcome, fact cards during the holding period.
- AI writes: what happened, what was right or wrong, what was missed, and ONE concrete reusable lesson (one actionable sentence, not generic).
Lesson book:
- New lessons start at a neutral score.
- Each decision records which lessons were in its prompt.
- When those trades close, update lesson scores (helpful if the trade beat the benchmark, unhelpful otherwise).
- Only the top lessons (e.g., 10) go into prompts. Low scorers are deactivated, not deleted.
- Periodically merge near-duplicates.
- Lessons are private per team by default; a setting allows a shared book.

## 11. Teams and evolution
Team config (config/teams.yaml): holding style (short or long), news vs. price-trend weighting, minimum source trust for evidence, minimum judge conviction to trade, model/provider per role, max candidates per day.
Start with 3 meaningfully different teams.
Monthly evolution, only after at least 3 months of live paper trading and enough closed trades (configurable):
- Rank by risk-adjusted return (Sharpe) vs. benchmark, not raw profit.
- Retire the worst team. Create a new one by copying the best team's config and mutating one or two settings.
- New team gets fresh imaginary money and inherits the parent's lesson book.
- Store lineage: parent, generation, what was mutated.

## 12. Quant model (Phase 6)
- Predict each stock's excess return over the next 20 trading days from historical daily data.
- Features by code: momentum over several windows, volatility, volume change, drawdown, distance from moving averages, sector-relative performance.
- Walk-forward validation only (train on past, test on the next period, roll forward). Never shuffle time-series data.
- Start simple (gradient boosting or regularized linear regression).
- Report honestly whether it beats a naive baseline.
- Its score is one more input to the Analyst. It never trades by itself.
- Retrain monthly in GitHub Actions.

## 13. Dashboard (PWA)
Mobile-first, installable on iPhone via Add to Home Screen, light and dark mode, read-only.
Pages:
1. Leaderboard: equity, total return, Sharpe, max drawdown, trade count per team vs. SPY, equity curve chart
2. Team detail: positions, recent trades, config, lineage
3. Decision viewer: analyst report, bull, bear, judge verdict, risk result
4. Lesson book: active lessons, scores, usage
5. Source trust: sources ranked by trust with claim counts
6. System: last run status, errors, AI spend this month vs. cap
Visible note: "Experimental simulation with imaginary money. Not investment advice."

## 14. AI cost control
- config/budget.yaml: monthly cap and per-run cap in USD.
- Track tokens and cost for every call in llm_usage.
- Budget guard checks before every AI call; skip and log if the cap would be exceeded.
- Cheap model for fact cards and post-mortems; stronger model only for the Judge.
- Keep prompts short; summarize instead of pasting full articles.
- Never process the same article twice.

## 15. Build phases (stop for approval after each)
Phase 1 — Foundation and data: repo structure, config files, .env.example, Supabase schema with RLS, price/news/EDGAR collectors, daily GitHub Actions workflow. Done when data lands in Supabase each trading day and tests pass.
Phase 2 — One team plus dashboard: fact cards, AI client with cost tracking and budget guard, one team (analyst, bull, bear, judge), risk engine, simulator, benchmark, dashboard (leaderboard, decision viewer, system page). Done when the team decides daily, orders fill at next open, and the dashboard works on a phone.
Phase 3 — Post-mortems and lesson book, plus lesson book page.
Phase 4 — Source trust scoring and corroboration, plus source trust page.
Phase 5 — Three teams and monthly evolution with safeguards and lineage, plus team detail page.
Phase 6 — Quant model with walk-forward evaluation report and monthly retraining.

## 16. Testing
- Unit tests: fills, fees, slippage, risk rules, stop-losses, trust updates, lesson scoring, Sharpe and drawdown, no-look-ahead filtering.
- A test that FAILS if any agent input contains data published after the decision time.
- --dry-run mode with mocked AI for zero-cost full pipeline tests.
- Run tests in GitHub Actions on every push.

## 17. Known limitations (state honestly in docs and dashboard)
- AI models don't truly retrain; their learning is the lesson book, trust scores, and evolving settings. Only the quant model retrains.
- Fake-information detection is probabilistic and will sometimes be wrong.
- Free data may be delayed, incomplete, or change.
- Paper trading misses some real-world effects.
- Strong results over a few months are likely luck; meaningful evaluation needs many months vs. the benchmark.
- AI API calls cost real money even though trading money is imaginary.

## 18. Design reference (owner's note)
Design the dashboard after the owner's Planner app (repo obopd/APP-Design). Not an exact copy;
adapt to this app's functions. Simple, modern, minimalistic, aesthetic. Carry over:
- Black & white by default, plenty of white space, small sizes, few words, icons over labels.
- Fraunces (semi-bold, upright) for headings and numbers; system sans-serif for small UI text.
- Light + dark mode (pure black dark background, raised surfaces for sheets).
- Floating rounded bottom dock for navigation; bottom sheets instead of native pop-ups.
- No build step: plain HTML/CSS/JS ES modules. Animate only transform/opacity; respect reduced motion.
- Gains/losses may use a muted green/red; everything else stays monochrome.
