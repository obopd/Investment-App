# Progress

## Done — Phase 1 (foundation and data)
- Repo structure, config files (`config/*.yaml`), `.env.example`, `docs/SETUP.md`.
- Database schema with Row Level Security on every table: `arena/db/schema.sql`.
- Collectors:
  - Prices: Tiingo, incremental with one-day overlap; refetches full history when a dividend/split changes adjusted prices.
  - News: 10 public RSS feeds (market news, press-release wires, Fed, five company newsrooms). robots.txt checked; only feed title + snippet stored.
  - Filings: SEC EDGAR 8-K / 10-Q / 10-K for the 30 stocks.
- `arena/pipeline.py` with `--date`, `--dry-run`, `--force`; skips weekends and NYSE holidays; safe to re-run; logs each run to `runs`.
- No-look-ahead helpers (`arena/lookahead.py`) + tests.
- GitHub Actions: tests on every push; daily run at 23:30 UTC on weekdays (plus manual "Run workflow").
- 39 tests passing.
- First live run 2026-11-03: status ok. 195,626 price rows (31 tickers), 179 articles from 10 feeds, 60 SEC filings.
- Supabase URL may be entered with or without `/rest/v1`.

## Done — Phase 2 (one team + dashboard), built in practice mode
- AI client (`arena/llm`): model per role from `config/models.yaml`, structured JSON output, validation with one retry, every call logged to `llm_usage`, budget guard checks the worst case before each call (monthly $5 / per run $0.50 by default).
- Modes: `practice` (fake AI answers, $0, teams labelled "(practice)") and `live` (needs `ANTHROPIC_API_KEY`). Live without a key skips AI steps; data collection continues.
- Fact cards (`arena/facts`): cheap model, 10 articles per call, each article processed once, near-duplicate claims merged with every source recorded. Practice cards are flagged and never shown to live teams; going live re-reads the last 3 days of articles with the real model.
- Team "Momentum": Analyst → Bull → Bear → Judge, inputs built by code with the no-look-ahead check.
- Risk engine (`arena/risk`): max weight 15%, max 10 positions, 5% cash buffer, max 2 new positions/day, stop-loss −12%, drawdown brake at −15% (releases at −10%). Can veto or resize.
- Simulator (`arena/sim`): fills at next open, 0.05% fee + 0.05% slippage, whole shares, buys reduced to fit cash, sells before buys.
- Equity, SPY buy-and-hold benchmark, leaderboard (return, Sharpe, max drawdown, trades).
- Dashboard PWA (`/dashboard`): Leaderboard, Decisions (tap for analyst/bull/bear/judge/risk), System (runs, errors, AI spend vs cap). Light/dark, installable. Published by the "Dashboard" workflow.
- 110 tests passing, including a multi-day simulation.
- Teams only decide after 4pm New York; a run started during market hours collects data and fills orders but skips decisions.

## Done — Phase 3 (post-mortems and lesson book)
- Every closed trade (each sell) gets a post-mortem from the cheap model: what happened, right/wrong, what was missed, and ONE lesson ("When <condition>, <action>."). Generic or multi-sentence lessons are rejected and retried once.
- Lesson score = (helpful + 1) / (used + 2): starts at 0.5. "Used" = closed trades whose opening decision had the lesson in its prompt; "helpful" = those that beat SPY over the same period. Scored once per closed trade, together with its post-mortem.
- Top 10 active lessons (written before the decision time) go into the analyst's prompt; each decision records which lessons it saw.
- Lessons with ≥4 uses and score < 0.35 are deactivated (never deleted); near-duplicates are merged; max 30 active per team. Settings in `config/learning.yaml`. Shared book via `shared_lessons: true` in a team's config.
- Dashboard: Lesson book tab (book icon) with scores, usage, inactive lessons and recent reviews.

## Done — Phase 4 (source trust and corroboration)
- Each live fact card is judged when its horizon (5 or 20 trading days) passes: stock return minus SPY from the last close before publication. Beyond ±1% (5d) / ±2% (20d) = correct/incorrect, otherwise unclear (no change). Settings in `config/trust.yaml`.
- A later claim from an official source (SEC filings, company newsrooms) with the same ticker and type but the opposite direction marks the earlier claim incorrect.
- Every source that made a claim gets the result: alpha = correct + 1, beta = incorrect + 1, trust = alpha / (alpha + beta). Practice cards are never scored, so trust only moves once real AI reads the news.
- Claims repeated by ≥2 sources with trust ≥ 0.5 are labelled corroborated; teams see the label.
- Dashboard: Source trust tab (shield icon), tap a source for its recent claims and outcomes.
- `--skip-collect` flag / workflow option for quick checks that don't use the Tiingo hourly limit.

## Done — Phase 5 (three teams, evolution, team detail)
- Three founding teams in `config/teams.yaml`: **Momentum** (short-term, price-trend leaning), **Newshound** (short-term, news-heavy, stricter source trust), **Steady** (long-term, fewer trades, higher conviction bar). Each can pick a model per role (default: cheap analyst/bull/bear, stronger judge).
- Monthly evolution (`config/evolution.yaml`), first trading day of the month, at most once: ranks teams by the Sharpe ratio of daily returns in excess of SPY, retires the worst, and creates a child of the best with 1–2 mutated settings, fresh $100k and a copy of the parent's lesson book. Lineage (parent, generation, what changed) is stored.
- Safeguards: live mode only, every team needs ≥63 trading days and ≥10 closed trades; mutations stay within bounds and are reproducible.
- Founders retired by evolution are not recreated; editing `teams.yaml` only changes founders (generation 0).
- Dashboard: tap a team on the leaderboard for positions, recent trades, settings and lineage; retired teams are listed.
- Practice stand-ins now follow each team's style, so practice teams differ.

## Done — Phase 6 (quant model)
- Features from daily prices only (`arena/quant/features.py`): momentum 5/20/60/120d, volatility 20/60d, volume change, 60d drawdown, distance from 20/50/200d averages, sector-relative and SPY-relative 20d return. Standardised across stocks each day.
- Target: next 20 trading days' return minus SPY's. Model: ridge regression (numpy only).
- Walk-forward evaluation from 2008: each month trains only on data whose outcome was known before that month (20-day purge gap), never shuffled. Compared with naive baselines (no skill, 60-day momentum). Honest verdict in `docs/quant_report.md`.
- Monthly retraining workflow ("Quant model", 1st of the month) commits `models/quant_model.json` and the report.
- The Analyst sees each stock's quant rank and predicted 20-day excess return as a minor hint; the model never trades by itself. No model file = no scores.

## Done — after Phase 6 (owner requests)
- Dashboard shows full company names everywhere instead of tickers; AI prompts ask for full names in written text.
- Decisions tab: team buttons (All / each team), "Planned" orders with the expected time (next market open, 9:30am New York, also in the viewer's own time zone), "Bought & sold" with shares, price and time, then the decision list.

- Teams renamed to **A** (short-term price trends, was Momentum), **B** (news, was Newshound), **C** (long-term, was Steady); no "(practice)" suffix. Teams keep their history: `config/teams.yaml` has a stable `key` and `formerly` names. Evolved children are named A2, A3...
- Dashboard tab switches are instant: answers are cached for a minute, other tabs are pre-loaded in the background, and a slow earlier load can't overwrite a newer tab.

## Next
All six phases are built. Remaining: switch to live AI when ready (see docs/SETUP.md section 7), then let it run for months before judging results.

## Data sources and terms
- **Tiingo** (prices): free plan is for personal/non-commercial use; raw data may not be redistributed. So `prices_daily` is not readable with the public dashboard key. Free limits are roughly 50 requests/hour and 1,000/day; a daily run uses ~31.
- **SEC EDGAR**: public. Requires a User-Agent with contact email and ≤10 requests/second (we do ~5/s).
- **RSS feeds**: publishers' own public feeds; we store only what the feed provides and link to the original.
  Not used because robots.txt disallows automated access: Yahoo Finance, Google News, Seeking Alpha, Motley Fool, Benzinga, Zacks.
  Turned off because they block automated readers (HTTP 403): CNBC, GlobeNewswire.

## Known issues / notes
- GitHub only runs scheduled workflows from the default branch, so the daily run starts after this branch is merged into `main`. On public repos GitHub also pauses schedules after 60 days without commits.
- Ticker tagging of general headlines uses names/symbols and will have some misses and false hits; later AI fact-card extraction decides relevance.
- Company newsroom feeds are mostly product/marketing posts (low signal); kept for now, trust scoring will judge them.
- Unscheduled market closures (e.g. a national day of mourning) must be added to `SPECIAL_CLOSURES` in `arena/market_calendar.py`. If missed, the run simply finds no new price bar.
- Repo is public (for free GitHub Pages). Keys live only in GitHub Secrets; the dashboard key is read-only by design.
- Practice-mode results are placeholders (a simple trend rule), not AI judgment.
- Simulated teams receive no dividends (raw prices); the SPY benchmark is treated the same way for fairness.
- Model prices in `config/models.yaml` were checked 2026-11; update them if Anthropic changes prices.
