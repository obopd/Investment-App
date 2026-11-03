import { q, qAll, inList } from "./api.js";
import { lineChart } from "./chart.js";
import { ago, day, dayYear, esc, money, num, pct, tone } from "./format.js";
import { openSheet } from "./sheet.js";

const DISCLAIMER = `<p class="note disclaimer">Experimental simulation with imaginary money. Not investment advice.</p>`;

// ---------- Leaderboard ----------
export async function leaderboard(el, setAsOf) {
  const [rows, teams] = await Promise.all([
    q("leaderboard", { select: "*" }),
    q("teams", { select: "id,name,config,status,generation,parent_team_id,mutation,created_at,retired_at", order: "id.asc" }),
  ]);
  allTeams = teams;
  const active = teams.filter((t) => t.status === "active");
  const teamRows = rows.filter((r) => !r.is_benchmark && active.some((t) => t.id === r.team_id))
    .sort((a, b) => (b.total_return ?? -1) - (a.total_return ?? -1));
  const spy = rows.find((r) => r.is_benchmark);
  if (!teamRows.length) {
    el.innerHTML = `<p class="empty">No results yet. The first evening run creates the teams.</p>${DISCLAIMER}`;
    return;
  }
  setAsOf(teamRows[0].as_of);
  const start = teamRows.map((r) => r.start_date).sort()[0];
  const [equity, bench] = await Promise.all([
    qAll("equity_daily", { select: "team_id,date,total_equity", team_id: inList(teamRows.map((r) => r.team_id)), order: "date.asc,team_id.asc" }),
    qAll("benchmark_daily", { select: "date,equity", date: `gte.${start}`, order: "date.asc" }),
  ]);
  const series = teamRows.map((r) => ({
    name: r.name, points: equity.filter((e) => e.team_id === r.team_id).map((e) => ({ date: e.date, value: e.total_equity })),
  }));
  series.push({ name: "SPY", bench: true, points: bench.map((b) => ({ date: b.date, value: b.equity })) });

  const top = teamRows[0];
  const vs = spy && top.total_return != null && spy.total_return != null ? top.total_return - spy.total_return : null;
  const practice = active.some((t) => t.config?.mode === "practice");
  const retired = teams.filter((t) => t.status === "retired" && t.config?.mode === active[0]?.config?.mode);
  const row = (r, i) => `
    <li class="team-row${r.is_benchmark ? " bench" : ""}"${r.is_benchmark ? "" : ` data-team="${r.team_id}" role="button" tabindex="0"`}>
      <span class="name"><span class="rank">${r.is_benchmark ? "–" : i + 1}</span>${esc(r.name)}</span>
      <span class="eq num">${money(r.equity)}</span>
      <span class="stats">
        <span>Return<b class="${tone(r.total_return)}">${pct(r.total_return)}</b></span>
        <span>Sharpe<b>${num(r.sharpe)}</b></span>
        <span>Max DD<b>${pct(r.max_drawdown)}</b></span>
        <span>Trades<b>${r.trades ?? 0}</b></span>
      </span>
    </li>`;
  el.innerHTML = `
    ${practice ? `<p class="chip" style="justify-self:start">Practice mode · placeholder decisions, no real AI</p>` : ""}
    <section class="hero">
      <p class="label">${esc(top.name)}</p>
      <span class="big num">${money(top.equity)}</span>
      <span class="sub"><span class="${tone(top.total_return)}">${pct(top.total_return)}</span>
        <span class="muted"> since ${day(top.start_date)}${vs == null ? "" : ` · ${pct(vs)} vs SPY`}</span></span>
    </section>
    <section>
      ${lineChart(series)}
      <div class="legend" style="margin-top:8px">${series.map((s, i) => `<span class="${s.bench ? "dash" : ""}"><i style="${s.bench ? "" : `background:var(--fg);opacity:${SERIES_OPACITY[i] ?? 0.3}`}"></i>${esc(s.name)}</span>`).join("")}</div>
    </section>
    <section>
      <p class="label">Leaderboard</p>
      <ul class="rows">${teamRows.map(row).join("")}${spy ? row(spy, 0) : ""}</ul>
      <p class="note" style="margin-top:8px">Tap a team for its positions, trades and settings. Sharpe needs months of data to mean anything; short-term results are mostly luck.</p>
    </section>
    ${retired.length ? `<section><p class="label">Retired teams</p><ul class="rows">${retired.map((t) => `
      <li class="team-row bench" data-team="${t.id}" role="button" tabindex="0"><span class="name">${esc(t.name)}</span>
        <span class="muted" style="text-align:right;font-size:12px">gen ${t.generation ?? 0} · retired ${day(t.retired_at)}</span></li>`).join("")}</ul></section>` : ""}
    ${DISCLAIMER}`;
  el.querySelectorAll("[data-team]").forEach((li) => li.addEventListener("click", () => showTeam(+li.dataset.team, rows)));
}

export const SERIES_OPACITY = [1, 0.55, 0.3, 0.2];
let allTeams = [];

const SETTING_LABELS = {
  holding_style: "Holding style", news_weight: "News weight", min_source_trust: "Min source trust",
  min_conviction: "Min conviction", max_candidates_per_day: "Candidates per day", fact_card_lookback_days: "News lookback (days)",
};

async function showTeam(id, boardRows) {
  const t = allTeams.find((x) => x.id === id);
  const [positions, trades, pf] = await Promise.all([
    q("positions", { select: "ticker,shares,avg_cost,opened_at", team_id: `eq.${id}`, order: "ticker.asc" }),
    q("trades", { select: "ticker,side,shares,fill_price,fees,executed_at", team_id: `eq.${id}`, order: "id.desc", limit: 15 }),
    q("portfolios", { select: "cash,start_date,brake_active", team_id: `eq.${id}` }),
  ]);
  const board = boardRows.find((r) => r.team_id === id);
  const parent = allTeams.find((x) => x.id === t.parent_team_id);
  const children = allTeams.filter((x) => x.parent_team_id === id);
  const c = t.config || {};
  const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
  openSheet(`
    <div class="sheet-title"><h2 class="display">${esc(t.name)}</h2><span class="chip">${esc(t.status)}</span></div>
    <p class="note" style="margin-top:-12px">Generation ${t.generation ?? 0} · started ${day(pf[0]?.start_date || t.created_at)}${c.mode === "practice" ? " · practice" : ""}${pf[0]?.brake_active ? " · drawdown brake on" : ""}</p>
    ${board ? `<div class="verdict"><div><span>Equity</span><b>${money(board.equity)}</b></div>
      <div><span>Return</span><b class="${tone(board.total_return)}">${pct(board.total_return)}</b></div>
      <div><span>Cash</span><b>${money(pf[0]?.cash)}</b></div></div>` : ""}
    <section><p class="label">Positions · ${positions.length}</p>
      ${positions.length ? positions.map((p) => kv(`<b class="num" style="color:var(--fg)">${esc(p.ticker)}</b> · since ${day(p.opened_at)}`, `${p.shares} @ ${num(p.avg_cost)}`)).join("") : `<p class="note">No positions.</p>`}
      <p class="note" style="margin-top:6px">Cost basis includes fees. Live prices aren't shown (data licence).</p>
    </section>
    <section><p class="label">Recent trades</p>
      ${trades.length ? trades.map((x) => kv(`<span class="${x.side === "buy" ? "up" : "down"}">${esc(x.side)}</span> ${esc(x.ticker)} · ${day(x.executed_at)}`, `${x.shares} @ ${num(x.fill_price)}`)).join("") : `<p class="note">No trades yet.</p>`}
    </section>
    <section><p class="label">Settings</p>
      ${Object.entries(SETTING_LABELS).filter(([k]) => k in c).map(([k, label]) => kv(label, esc(c[k]))).join("")}
      ${c.models ? kv("Judge model", esc(c.models.judge || "default")) : ""}
    </section>
    <section><p class="label">Lineage</p>
      ${kv("Parent", parent ? esc(parent.name) : "founder")}
      ${t.mutation ? kv("Changed", esc(t.mutation)) : ""}
      ${children.length ? kv("Children", children.map((x) => esc(x.name)).join(", ")) : ""}
    </section>`);
}

// ---------- Decisions ----------
const PAGE = 40;
let decOffset = 0;
let decCache = [];
let teamNames = {};

function actionClass(a = "") {
  return a.startsWith("buy") ? "buy" : a.startsWith("sell") ? "sell" : "";
}

function chipText(d) {
  const a = d.final_action || "";
  if (a.startsWith("buy") || a.startsWith("sell")) return a.split(" ")[0];
  if (a.startsWith("vetoed")) return "vetoed";
  if (a.startsWith("skipped")) return "skipped";
  return a.split(":")[0] || "–";
}

export async function decisions(el, setAsOf, { more = false } = {}) {
  if (!more) { decOffset = 0; decCache = []; }
  const [rows, teams] = await Promise.all([
    q("decisions", { select: "id,team_id,date,ticker,final_action,judge_verdict,risk_result", order: "date.desc,id.desc", limit: PAGE, offset: decOffset }),
    more ? Promise.resolve(null) : q("teams", { select: "id,name" }),
  ]);
  if (teams) teamNames = Object.fromEntries(teams.map((t) => [t.id, t.name]));
  decCache = decCache.concat(rows);
  decOffset += rows.length;
  if (!decCache.length) { el.innerHTML = `<p class="empty">No decisions yet.</p>${DISCLAIMER}`; return; }
  setAsOf(decCache[0].date);
  const byDay = {};
  for (const d of decCache) (byDay[d.date] ||= []).push(d);
  el.innerHTML = Object.entries(byDay).map(([date, ds]) => `
    <section class="day-group">
      <p class="label">${dayYear(date)}</p>
      <ul class="rows">${ds.map((d) => `
        <li><button class="dec-row" data-id="${d.id}">
          <span class="tk num">${esc(d.ticker || "—")}</span>
          <span class="mid">
            <span class="act">${esc(d.final_action || "")}</span>
            <span class="meta">${esc(teamNames[d.team_id] || "")}${d.judge_verdict ? ` · conviction ${num(d.judge_verdict.conviction)}` : ""}</span>
          </span>
          <span class="chip ${actionClass(d.final_action)}">${esc(chipText(d))}</span>
        </button></li>`).join("")}
      </ul>
    </section>`).join("") + (rows.length === PAGE ? `<button class="more" id="more">Show more</button>` : "") + DISCLAIMER;
  el.querySelectorAll(".dec-row").forEach((b) => b.addEventListener("click", () => showDecision(+b.dataset.id)));
  el.querySelector("#more")?.addEventListener("click", () => decisions(el, setAsOf, { more: true }));
}

async function showDecision(id) {
  const [d] = await q("decisions", { select: "*", id: `eq.${id}` });
  const v = d.judge_verdict, r = d.risk_result;
  const text = [d.analyst_report, d.bull_case, d.bear_case, v?.reasoning, ...(v?.key_evidence || [])].join(" ");
  const ids = [...new Set([...text.matchAll(/#(\d+)/g)].map((m) => +m[1]))].slice(0, 20);
  const cards = ids.length ? await q("fact_cards", { select: "id,ticker,claim,direction,claim_type", id: inList(ids) }) : [];
  const block = (title, body) => body ? `<section class="section"><p class="label">${title}</p><p class="text">${esc(body)}</p></section>` : "";
  openSheet(`
    <div class="sheet-title"><h2 class="display">${esc(d.ticker || "Summary")}</h2>
      <span class="chip ${actionClass(d.final_action)}">${esc(d.final_action || "")}</span></div>
    <p class="note" style="margin-top:-12px">${esc(teamNames[d.team_id] || "")} · ${dayYear(d.date)} · orders fill at the next open</p>
    ${v ? `<div class="verdict">
      <div><span>Judge</span><b>${esc(v.action)}</b></div>
      <div><span>Conviction</span><b>${num(v.conviction)}</b></div>
      <div><span>Target weight</span><b>${pct(v.target_weight, 0).replace("+", "")}</b></div></div>` : ""}
    ${block("Judge", v?.reasoning)}
    ${v?.main_risk ? block("Main risk", v.main_risk) : ""}
    ${cards.length ? `<section class="section"><p class="label">Evidence</p><ul class="evidence">${cards.map((c) => `
      <li class="text">#${c.id} ${esc(c.claim)}<small>${esc(c.ticker || "macro")} · ${esc(c.direction)} · ${esc(c.claim_type)}</small></li>`).join("")}</ul></section>` : ""}
    ${block("Analyst", d.analyst_report)}
    ${block("Bull", d.bull_case)}
    ${block("Bear", d.bear_case)}
    ${r ? `<section class="section"><p class="label">Risk engine</p><p class="text"><b>${esc(r.status)}</b> · ${esc(r.reason)}</p></section>` : ""}
  `);
}

// ---------- System ----------
export async function system(el, setAsOf) {
  const runs = await q("runs", { select: "id,run_date,started_at,finished_at,status,dry_run,error,summary", order: "id.desc", limit: 20 });
  const real = runs.filter((r) => r.status !== "skipped");
  const last = real[0];
  if (!last) { el.innerHTML = `<p class="empty">No runs yet.</p>${DISCLAIMER}`; return; }
  setAsOf(last.run_date);
  const ai = last.summary?.ai || {};
  const spent = ai.month_spent_usd ?? 0, cap = ai.monthly_cap_usd ?? 0;
  const s = last.summary || {};
  const kv = (k, v) => `<div class="kv"><span>${k}</span><span class="num">${typeof v === "number" ? v.toLocaleString("en-US") : v}</span></div>`;
  el.innerHTML = `
    <section class="card">
      <div class="status"><span class="dot ${esc(last.status)}"></span><b>Last run ${esc(last.status)}</b>
        <span class="muted" style="margin-left:auto">${ago(last.finished_at || last.started_at)}</span></div>
      <p class="note" style="margin-top:4px">${dayYear(last.run_date)} · runs every weekday after the US close</p>
    </section>
    <section>
      <p class="label">AI spend this month</p>
      ${ai.mode ? `
      <span class="num" style="font-size:28px">$${spent.toFixed(2)}</span><span class="muted"> of $${cap.toFixed(2)} cap</span>
      <div class="meter"><i style="transform:scaleX(${cap ? Math.min(spent / cap, 1) : 0})"></i></div>
      <p class="note" style="margin-top:8px">Mode: ${esc(ai.mode)}${ai.mode === "practice" ? " (fake AI answers, $0)" : ""}${ai.available === false ? " · AI off (no key)" : ""}${ai.skipped_calls ? ` · ${ai.skipped_calls} call(s) skipped by the cap` : ""}</p>`
      : `<p class="muted" style="margin:0">No AI used yet.</p>`}
    </section>
    <section>
      <p class="label">Last run</p>
      ${kv("Price rows", s.prices?.rows ?? "–")}
      ${kv("New articles", s.news?.new_articles ?? "–")}
      ${kv("New filings", s.filings?.new_filings ?? "–")}
      ${kv("Fact cards", s.fact_cards ? `${s.fact_cards.cards_new} new · ${s.fact_cards.cards_merged} merged` : "–")}
      ${kv("AI calls", ai.calls ?? 0)}
      ${s.quant ? kv("Quant model scores", s.quant.available ? `as of ${day(s.quant.as_of)}` : "no model yet") : ""}
    </section>
    <section>
      <p class="label">Recent runs</p>
      <ul class="rows">${runs.map((r) => `<li><button class="run-row" data-id="${r.id}">
        <span class="dot ${esc(r.status)}"></span><span class="when">${dayYear(r.run_date)}${r.dry_run ? " · test" : ""}</span>
        <span class="muted">${esc(r.status)}</span></button></li>`).join("")}</ul>
    </section>
    <section class="section">
      <p class="label">Limits of this experiment</p>
      <p class="note">AI models don't retrain; they learn only through lessons, trust scores and settings. Source trust is a statistical signal and will sometimes be wrong. Free data can be late or incomplete. Paper trading misses real-world costs. A few months of good results is likely luck. AI calls cost real money even though trading money is imaginary.</p>
    </section>
    ${DISCLAIMER}`;
  el.querySelectorAll(".run-row").forEach((b) => b.addEventListener("click", () => {
    const r = runs.find((x) => x.id === +b.dataset.id);
    let errors = r.error;
    try { errors = JSON.stringify(JSON.parse(r.error), null, 1); } catch { /* plain text */ }
    openSheet(`<div class="sheet-title"><h2 class="display">${dayYear(r.run_date)}</h2><span class="chip">${esc(r.status)}</span></div>
      ${r.error ? `<section><p class="label">Errors</p><pre class="err text">${esc(errors)}</pre></section>` : `<p class="muted">No errors.</p>`}
      <section><p class="label">Summary</p><pre class="err text" style="color:var(--fg)">${esc(JSON.stringify(r.summary, null, 1))}</pre></section>`);
  }));
}

// ---------- Lesson book ----------
export async function lessons(el, setAsOf) {
  const teams = await q("teams", { select: "id,name,config", status: "eq.active", order: "id.asc" });
  if (!teams.length) { el.innerHTML = `<p class="empty">No teams yet.</p>${DISCLAIMER}`; return; }
  const ids = teams.map((t) => t.id);
  const [rows, reviews] = await Promise.all([
    q("lessons", { select: "id,team_id,text,score,times_used,times_helpful,active,post_mortem_id,created_at", order: "score.desc,id.desc", limit: 400 }),
    q("post_mortems", { select: "id,closed_trade_id,team_id,created_at", team_id: inList(ids), order: "id.desc", limit: 12 }),
  ]);
  const trades = reviews.length ? await q("closed_trades", { select: "id,ticker,pnl_pct,benchmark_return,exit_date", id: inList(reviews.map((r) => r.closed_trade_id)) }) : [];
  const tradeOf = Object.fromEntries(trades.map((t) => [t.id, t]));
  const mine = rows.filter((l) => l.team_id == null || ids.includes(l.team_id));
  if (reviews[0]) setAsOf(reviews[0].created_at);
  const lessonRow = (l) => `
    <li><button class="lesson-row" data-id="${l.id}">
      <span class="lt text">${esc(l.text)}</span>
      <span class="meta"><span class="score-bar"><i style="transform:scaleX(${l.score})"></i></span>
        <span class="num">${num(l.score)}</span> · used ${l.times_used} · helped ${l.times_helpful}</span>
    </button></li>`;
  const section = (t) => {
    const book = mine.filter((l) => l.team_id === t.id || (l.team_id == null && t.config?.shared_lessons));
    const active = book.filter((l) => l.active), off = book.filter((l) => !l.active);
    return `<section>
      <p class="label">${esc(t.name)} · ${active.length} active</p>
      ${active.length ? `<ul class="rows">${active.map(lessonRow).join("")}</ul>` : `<p class="note">No lessons yet. They appear after the first trades close.</p>`}
      ${off.length ? `<details class="inactive"><summary class="note">${off.length} inactive</summary><ul class="rows">${off.map(lessonRow).join("")}</ul></details>` : ""}
    </section>`;
  };
  el.innerHTML = `
    <p class="note">Each closed trade gets a review and one lesson. A lesson's score rises when trades that used it beat SPY. Only the top ${10} go into a team's prompts.</p>
    ${teams.map(section).join("")}
    <section>
      <p class="label">Recent reviews</p>
      ${reviews.length ? `<ul class="rows">${reviews.map((r) => { const t = tradeOf[r.closed_trade_id] || {}; return `
        <li><button class="dec-row pm-row" data-id="${r.id}">
          <span class="tk num">${esc(t.ticker || "—")}</span>
          <span class="mid"><span class="act ${tone(t.pnl_pct)}">${pct(t.pnl_pct)}</span>
            <span class="meta">vs SPY ${pct(t.benchmark_return)} · closed ${day(t.exit_date)}</span></span>
        </button></li>`; }).join("")}</ul>` : `<p class="note">No closed trades yet.</p>`}
    </section>
    ${DISCLAIMER}`;
  el.querySelectorAll(".lesson-row").forEach((b) => b.addEventListener("click", () => {
    const l = mine.find((x) => x.id === +b.dataset.id);
    showReview(l.post_mortem_id, l);
  }));
  el.querySelectorAll(".pm-row").forEach((b) => b.addEventListener("click", () => showReview(+b.dataset.id)));
}

async function showReview(pmId, lesson) {
  const [pm] = pmId ? await q("post_mortems", { select: "*", id: `eq.${pmId}` }) : [null];
  const [t] = pm ? await q("closed_trades", { select: "*", id: `eq.${pm.closed_trade_id}` }) : [null];
  const block = (title, body) => body ? `<section class="section"><p class="label">${title}</p><p class="text">${esc(body)}</p></section>` : "";
  openSheet(`
    <div class="sheet-title"><h2 class="display">${esc(t?.ticker || "Lesson")}</h2>
      ${t ? `<span class="chip ${t.pnl_pct >= 0 ? "buy" : "sell"}">${pct(t.pnl_pct)}</span>` : ""}</div>
    ${t ? `<p class="note" style="margin-top:-12px">${day(t.entry_date)} → ${day(t.exit_date)} · ${t.holding_days} days · SPY ${pct(t.benchmark_return)}</p>` : ""}
    ${lesson ? `<div class="verdict"><div><span>Score</span><b>${num(lesson.score)}</b></div>
      <div><span>Used</span><b>${lesson.times_used}</b></div><div><span>Helped</span><b>${lesson.times_helpful}</b></div></div>` : ""}
    ${block("Lesson", lesson?.text || pm?.lesson_candidate)}
    ${block("What happened", pm?.what_happened)}
    ${block("Right or wrong", pm?.right_or_wrong)}
    ${block("What was missed", pm?.what_was_missed)}
    ${!pm ? `<p class="muted">No review found.</p>` : ""}`);
}

// ---------- Source trust ----------
export async function sources(el) {
  const [rows, teams] = await Promise.all([
    q("sources", { select: "id,name,domain,trust_alpha,trust_beta,trust_score,claims_evaluated", order: "claims_evaluated.desc,trust_score.desc", limit: 300 }),
    q("teams", { select: "config", status: "eq.active" }),
  ]);
  const practice = teams.length && teams.every((t) => t.config?.mode === "practice");
  const rated = rows.filter((s) => s.claims_evaluated > 0).sort((a, b) => b.trust_score - a.trust_score || b.claims_evaluated - a.claims_evaluated);
  const unrated = rows.filter((s) => !s.claims_evaluated);
  const row = (s) => `
    <li><button class="src-row" data-id="${s.id}">
      <span class="mid"><span class="act">${esc(s.name)}</span><span class="meta">${esc(s.domain)}${s.claims_evaluated ? ` · ${Math.round(s.trust_alpha - 1)} right · ${Math.round(s.trust_beta - 1)} wrong` : ""}</span></span>
      <span class="score-bar"><i style="transform:scaleX(${s.trust_score ?? 0.5})"></i></span>
      <span class="num trust">${num(s.trust_score ?? 0.5)}</span>
    </button></li>`;
  el.innerHTML = `
    <p class="note">A source's trust rises when its claims come true (stock vs SPY after 5 or 20 trading days) and falls when they don't. New sources start at 0.50. A price move doesn't prove a claim true or false; trust is a statistical signal that only means something over many claims.</p>
    ${practice ? `<p class="chip" style="justify-self:start">Practice mode · trust scoring starts with real AI</p>` : ""}
    <section><p class="label">Rated · ${rated.length}</p>
      ${rated.length ? `<ul class="rows">${rated.map(row).join("")}</ul>` : `<p class="note">No claims have matured yet. The first ones settle 5 trading days after real AI starts reading the news.</p>`}</section>
    <section><p class="label">Not rated yet · ${unrated.length}</p><ul class="rows">${unrated.map(row).join("")}</ul></section>
    ${DISCLAIMER}`;
  el.querySelectorAll(".src-row").forEach((b) => b.addEventListener("click", () => showSource(rows.find((s) => s.id === +b.dataset.id))));
}

async function showSource(s) {
  const links = await q("fact_card_sources", { select: "fact_card_id", source_id: `eq.${s.id}`, order: "fact_card_id.desc", limit: 25 });
  const ids = links.map((l) => l.fact_card_id);
  const [cards, evals] = ids.length ? await Promise.all([
    q("fact_cards", { select: "id,ticker,claim,direction,horizon_days,corroborated,published_at", id: inList(ids), order: "id.desc" }),
    q("claim_evaluations", { select: "fact_card_id,outcome,excess_return", fact_card_id: inList(ids) }),
  ]) : [[], []];
  const ev = Object.fromEntries(evals.map((e) => [e.fact_card_id, e]));
  const badge = (e) => !e ? `<span class="chip">pending</span>` : `<span class="chip ${e.outcome === "correct" ? "buy" : e.outcome === "incorrect" ? "sell" : ""}">${esc(e.outcome)}</span>`;
  openSheet(`
    <div class="sheet-title"><h2 class="display">${esc(s.name)}</h2><span class="chip">${num(s.trust_score ?? 0.5)}</span></div>
    <p class="note" style="margin-top:-12px">${esc(s.domain)} · ${s.claims_evaluated} claim(s) judged</p>
    <section><p class="label">Recent claims</p>
      ${cards.length ? `<ul class="evidence">${cards.map((c) => `<li class="text">${esc(c.claim)}
        <small>${esc(c.ticker || "macro")} · ${esc(c.direction)} · ${c.horizon_days}d${c.corroborated ? " · corroborated" : ""}${ev[c.id]?.excess_return != null ? ` · vs SPY ${pct(ev[c.id].excess_return)}` : ""} ${badge(ev[c.id])}</small></li>`).join("")}</ul>` : `<p class="note">No claims yet.</p>`}
    </section>`);
}
