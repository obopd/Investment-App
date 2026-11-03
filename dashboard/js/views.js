import { q, inList } from "./api.js";
import { lineChart } from "./chart.js";
import { ago, day, dayYear, esc, money, num, pct, tone } from "./format.js";
import { openSheet } from "./sheet.js";

const DISCLAIMER = `<p class="note disclaimer">Experimental simulation with imaginary money. Not investment advice.</p>`;

// ---------- Leaderboard ----------
export async function leaderboard(el, setAsOf) {
  const [rows, teams] = await Promise.all([
    q("leaderboard", { select: "*" }),
    q("teams", { select: "id,name,config", status: "eq.active" }),
  ]);
  const teamRows = rows.filter((r) => !r.is_benchmark && teams.some((t) => t.id === r.team_id))
    .sort((a, b) => (b.total_return ?? -1) - (a.total_return ?? -1));
  const spy = rows.find((r) => r.is_benchmark);
  if (!teamRows.length) {
    el.innerHTML = `<p class="empty">No results yet. The first daily run creates the team.</p>${DISCLAIMER}`;
    return;
  }
  setAsOf(teamRows[0].as_of);
  const start = teamRows.map((r) => r.start_date).sort()[0];
  const [equity, bench] = await Promise.all([
    q("equity_daily", { select: "team_id,date,total_equity", team_id: inList(teamRows.map((r) => r.team_id)), order: "date.asc" }),
    q("benchmark_daily", { select: "date,equity", date: `gte.${start}`, order: "date.asc" }),
  ]);
  const series = teamRows.map((r) => ({
    name: r.name, points: equity.filter((e) => e.team_id === r.team_id).map((e) => ({ date: e.date, value: e.total_equity })),
  }));
  series.push({ name: "SPY", bench: true, points: bench.map((b) => ({ date: b.date, value: b.equity })) });

  const top = teamRows[0];
  const vs = spy && top.total_return != null && spy.total_return != null ? top.total_return - spy.total_return : null;
  const practice = teams.some((t) => t.config?.mode === "practice");
  const row = (r, i) => `
    <li class="team-row${r.is_benchmark ? " bench" : ""}">
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
      <div class="legend" style="margin-top:8px">${series.map((s) => `<span class="${s.bench ? "dash" : ""}"><i style="${s.bench ? "" : "background:var(--fg)"}"></i>${esc(s.name)}</span>`).join("")}</div>
    </section>
    <section>
      <p class="label">Leaderboard</p>
      <ul class="rows">${teamRows.map(row).join("")}${spy ? row(spy, 0) : ""}</ul>
      <p class="note" style="margin-top:8px">Sharpe needs months of data to mean anything. Short-term results are mostly luck.</p>
    </section>
    ${DISCLAIMER}`;
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
  const kv = (k, v) => `<div class="kv"><span>${k}</span><span class="num">${v}</span></div>`;
  el.innerHTML = `
    <section class="card">
      <div class="status"><span class="dot ${esc(last.status)}"></span><b>Last run ${esc(last.status)}</b>
        <span class="muted" style="margin-left:auto">${ago(last.finished_at || last.started_at)}</span></div>
      <p class="note" style="margin-top:4px">${dayYear(last.run_date)} · runs every weekday after the US close</p>
    </section>
    <section>
      <p class="label">AI spend this month</p>
      <span class="num" style="font-size:28px">$${spent.toFixed(2)}</span><span class="muted"> of $${cap.toFixed(2)} cap</span>
      <div class="meter"><i style="transform:scaleX(${cap ? Math.min(spent / cap, 1) : 0})"></i></div>
      <p class="note" style="margin-top:8px">Mode: ${esc(ai.mode || "–")}${ai.mode === "practice" ? " (fake AI answers, $0)" : ""}${ai.available === false ? " · AI off (no key)" : ""}${ai.skipped_calls ? ` · ${ai.skipped_calls} call(s) skipped by the cap` : ""}</p>
    </section>
    <section>
      <p class="label">Last run</p>
      ${kv("Price rows", s.prices?.rows ?? "–")}
      ${kv("New articles", s.news?.new_articles ?? "–")}
      ${kv("New filings", s.filings?.new_filings ?? "–")}
      ${kv("Fact cards", s.fact_cards ? `${s.fact_cards.cards_new} new · ${s.fact_cards.cards_merged} merged` : "–")}
      ${kv("AI calls", ai.calls ?? 0)}
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
