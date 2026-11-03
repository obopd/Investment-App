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
- 38 tests passing.

## Next — Phase 2
Fact cards, AI client with cost tracking and budget guard, first team, risk engine, simulator, benchmark, dashboard.

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
- Dashboard hosting: GitHub Pages on a private repo needs a paid plan. Decide in Phase 2 (make repo public, or another free host).
