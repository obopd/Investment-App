// Currency switch: show money in US dollars or the viewer's local currency.
// The simulation itself always runs in US dollars; this only changes how amounts are shown.
// Rates: ExchangeRate-API's free daily feed (no key), with a second free source as backup.
const SOURCES = [
  { url: "https://open.er-api.com/v6/latest/USD", read: (d, c) => d.rates?.[c] },
  { url: "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.min.json", read: (d, c) => d.usd?.[c.toLowerCase()] },
];
const BY_ZONE = {
  "Asia/Seoul": "KRW", "Asia/Tokyo": "JPY", "Asia/Shanghai": "CNY", "Asia/Hong_Kong": "HKD",
  "Asia/Singapore": "SGD", "Asia/Kolkata": "INR", "Asia/Taipei": "TWD", "Europe/London": "GBP",
  "America/Toronto": "CAD", "America/Vancouver": "CAD", "Australia/Sydney": "AUD", "Australia/Melbourne": "AUD",
};

export function localCurrency() {
  let zone = "";
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { /* old browser */ }
  if (BY_ZONE[zone]) return BY_ZONE[zone];
  if (zone.startsWith("Europe/")) return "EUR";
  return "KRW";
}

const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// Rate for 1 USD in `code`, cached for 12 hours.
export async function usdRate(code) {
  const cached = store.get(`arena.fx.${code}`);
  if (cached && Date.now() - cached.at < 12 * 3600_000) return cached;
  for (const s of SOURCES) {
    try {
      const r = await fetch(s.url);
      if (!r.ok) continue;
      const rate = s.read(await r.json(), code);
      if (rate > 0) {
        const out = { rate, at: Date.now() };
        store.set(`arena.fx.${code}`, out);
        return out;
      }
    } catch { /* try the next source */ }
  }
  return cached || null;   // an older rate beats none
}

export const savedChoice = () => store.get("arena.currency") || "USD";
export const saveChoice = (code) => store.set("arena.currency", code);
