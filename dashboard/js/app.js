import { clearCache, configured } from "./api.js";
import { day, display } from "./format.js";
import { localCurrency, saveChoice, savedChoice, usdRate } from "./fx.js";
import * as views from "./views.js";

const view = document.getElementById("view");
const asof = document.getElementById("asof");
const refresh = document.getElementById("refresh");
const tabs = [...document.querySelectorAll(".tab")];
const RENDER = { board: views.leaderboard, decisions: views.decisions, lessons: views.lessons, sources: views.sources, system: views.system };

const setAsOf = (iso) => { asof.textContent = iso ? `as of ${day(iso)}` : ""; };

let current = 0;   // only the latest tap may draw; a slower earlier load is thrown away

async function show(name, { animate = true } = {}) {
  if (!RENDER[name]) name = "board";
  const token = ++current;
  tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  if (!configured) {
    view.innerHTML = `<p class="empty">Not connected yet. The dashboard workflow fills in the Supabase address and public key.</p>`;
    return;
  }
  // Draw into a fresh box off-screen, then swap it in whole: no half-drawn pages.
  const box = document.createElement("div");
  box.className = "view-box";
  const spin = setTimeout(() => refresh.classList.add("spin"), 150);   // only for slow loads
  try {
    await RENDER[name](box, (iso) => { if (token === current) setAsOf(iso); });
  } catch (e) {
    box.innerHTML = `<p class="empty">Couldn't load data.<br><span class="note">${String(e.message).replace(/</g, "&lt;")}</span></p>`;
  } finally {
    clearTimeout(spin);
    if (token === current) refresh.classList.remove("spin");
  }
  if (token !== current) return;
  view.replaceChildren(box);
  if (animate) { box.classList.add("view-enter"); }
}

// Warm the other tabs in the background after the first page is up.
function prefetch() {
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 300));
  idle(() => Object.entries(RENDER).forEach(([name, fn]) => {
    if (name !== location.hash.slice(1)) fn(document.createElement("div"), () => {}).catch(() => {});
  }));
}

// ---------- Currency button: $ <-> local currency ----------
const curBtn = document.getElementById("currency");
const LOCAL = localCurrency();
const symbol = (code) => new Intl.NumberFormat("en-US", { style: "currency", currency: code, currencyDisplay: "narrowSymbol" })
  .formatToParts(0).find((p) => p.type === "currency")?.value || code;

async function setCurrency(code, { redraw = true } = {}) {
  if (code === "USD") {
    Object.assign(display, { code: "USD", rate: 1 });
  } else {
    const fx = await usdRate(code);
    if (!fx) {   // no rate available: stay in dollars and say so briefly
      curBtn.classList.add("shake");
      setTimeout(() => curBtn.classList.remove("shake"), 500);
      asof.textContent = "exchange rate unavailable";
      return;
    }
    Object.assign(display, { code, rate: fx.rate, at: fx.at });
  }
  curBtn.textContent = symbol(display.code);
  curBtn.setAttribute("aria-label", `Showing ${display.code}. Tap to switch to ${display.code === "USD" ? LOCAL : "USD"}`);
  saveChoice(display.code);
  if (redraw) show(location.hash.slice(1), { animate: false });
}
curBtn.addEventListener("click", () => setCurrency(display.code === "USD" ? LOCAL : "USD"));

let hiddenAt = 0;
tabs.forEach((t) => t.addEventListener("click", () => { window.scrollTo(0, 0); show(t.dataset.tab); }));
refresh.addEventListener("click", () => { clearCache(); show(location.hash.slice(1), { animate: false }); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { hiddenAt = Date.now(); return; }
  if (Date.now() - hiddenAt > 60_000) { clearCache(); show(location.hash.slice(1), { animate: false }); }
});
window.addEventListener("hashchange", () => show(location.hash.slice(1)));
const startCurrency = savedChoice() === "USD" ? Promise.resolve() : setCurrency(LOCAL, { redraw: false });
startCurrency.finally(() => show(location.hash.slice(1)).then(prefetch));

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
