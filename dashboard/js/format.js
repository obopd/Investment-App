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

export const money2 = (x) => x == null ? "–" : "$" + x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// The US market opens at 9:30am New York time. Find that moment for a date, daylight saving included.
export function marketOpen(iso) {
  const d = String(iso).slice(0, 10);
  const fmt = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  for (const utcHour of ["13", "14"]) {
    const t = new Date(`${d}T${utcHour}:30:00Z`);
    if (fmt.format(t) === "09:30") return t;
  }
  return new Date(`${d}T14:30:00Z`);
}
const TIME = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
export const nyTime = (t) => t.toLocaleString("en-US", { ...TIME, timeZone: "America/New_York" });
export const localTime = (t) => t.toLocaleString("en-US", TIME);
