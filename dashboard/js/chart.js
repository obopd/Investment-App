// Tiny SVG line chart: % return since each series' start. No library needed.
import { esc, day } from "./format.js";

export function lineChart(series, { height = 170 } = {}) {
  // series: [{ name, points: [{date, value}], bench }]
  const all = series.flatMap((s) => s.points);
  if (all.length < 2) return `<p class="note">The chart appears after two trading days.</p>`;
  const dates = [...new Set(all.map((p) => p.date))].sort();
  const x = (d) => (dates.indexOf(d) / Math.max(dates.length - 1, 1)) * 100;
  const rets = series.map((s) => s.points.map((p) => ({ date: p.date, r: p.value / s.points[0].value - 1 })));
  const vals = rets.flat().map((p) => p.r);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (hi - lo < 0.01) { hi += 0.005; lo -= 0.005; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const y = (r) => ((hi - r) / (hi - lo)) * height;
  const lines = rets.map((pts, i) => {
    const d = pts.map((p, j) => `${j ? "L" : "M"}${x(p.date).toFixed(2)},${y(p.r).toFixed(2)}`).join("");
    const cls = series[i].bench ? "line bench" : "line";
    const stroke = series[i].bench ? "" : ` style="stroke:var(--fg);opacity:${[1, 0.55, 0.3, 0.2][i] ?? 0.2}"`;
    return `<path class="${cls}" d="${d}"${stroke}/>`;
  }).join("");
  return `<svg class="chart" viewBox="0 0 100 ${height}" preserveAspectRatio="none" role="img" aria-label="Return since start">
      <line class="zero" x1="0" x2="100" y1="${y(0)}" y2="${y(0)}"/>
      ${lines}
    </svg>
    <div class="legend" style="justify-content:space-between"><span>${esc(day(dates[0]))}</span><span>${esc(day(dates.at(-1)))}</span></div>`;
}
