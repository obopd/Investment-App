// Read-only access to Supabase's REST API with the public (publishable) key.
import { SUPABASE_URL, SUPABASE_KEY } from "../config.js";

const base = SUPABASE_URL.trim().replace(/\/+$/, "").replace(/\/rest\/v1$/, "") + "/rest/v1/";
export const configured = Boolean(SUPABASE_URL && SUPABASE_KEY);

// Answers are kept for a minute so switching tabs is instant. Data only changes once a day
// (after the evening run), and the refresh button clears this.
const TTL_MS = 60_000;
const cache = new Map();
export const clearCache = () => cache.clear();

export async function q(table, params = {}) {
  const url = new URL(base + table);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const key = url.toString();
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.data;   // a value or an in-flight promise
  const headers = { apikey: SUPABASE_KEY };
  // Legacy anon keys are JWTs and also go in Authorization; new sb_publishable_ keys don't.
  if (SUPABASE_KEY.startsWith("eyJ")) headers.Authorization = `Bearer ${SUPABASE_KEY}`;
  const data = fetch(url, { headers }).then(async (r) => {
    if (!r.ok) throw new Error(`${table}: HTTP ${r.status}`);
    return r.json();
  });
  cache.set(key, { at: Date.now(), data });
  data.catch(() => cache.delete(key));   // never keep a failure
  return data;
}

export const inList = (ids) => `in.(${[...new Set(ids)].join(",")})`;

// Supabase returns at most 1000 rows per request; page through longer results (needs an order).
export async function qAll(table, params = {}) {
  const out = [];
  for (let offset = 0; ; offset += 1000) {
    const page = await q(table, { ...params, limit: 1000, offset });
    out.push(...page);
    if (page.length < 1000) return out;
  }
}
