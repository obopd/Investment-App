// Read-only access to Supabase's REST API with the public (publishable) key.
import { SUPABASE_URL, SUPABASE_KEY } from "../config.js";

const base = SUPABASE_URL.trim().replace(/\/+$/, "").replace(/\/rest\/v1$/, "") + "/rest/v1/";
export const configured = Boolean(SUPABASE_URL && SUPABASE_KEY);

export async function q(table, params = {}) {
  const url = new URL(base + table);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const headers = { apikey: SUPABASE_KEY };
  // Legacy anon keys are JWTs and also go in Authorization; new sb_publishable_ keys don't.
  if (SUPABASE_KEY.startsWith("eyJ")) headers.Authorization = `Bearer ${SUPABASE_KEY}`;
  const r = await fetch(url, { headers });
  if (!r.ok) throw new Error(`${table}: HTTP ${r.status}`);
  return r.json();
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
