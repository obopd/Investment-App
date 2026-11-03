export const money = (x) => x == null ? "–" : "$" + Math.round(x).toLocaleString("en-US");
export const pct = (x, digits = 1) => x == null ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x * 100).toFixed(digits) + "%";
export const num = (x, digits = 2) => x == null ? "–" : x.toFixed(digits);
export const tone = (x) => x > 0 ? "up" : x < 0 ? "down" : "";
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const day = (iso) => { if (!iso) return "–"; const [y, m, d] = iso.slice(0, 10).split("-"); return `${MONTHS[+m - 1]} ${+d}`; };
export const dayYear = (iso) => iso ? `${day(iso)}, ${iso.slice(0, 4)}` : "–";
export const ago = (iso) => {
  if (!iso) return "–";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
};
