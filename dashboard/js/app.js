import { configured } from "./api.js";
import { day } from "./format.js";
import * as views from "./views.js";

const view = document.getElementById("view");
const asof = document.getElementById("asof");
const refresh = document.getElementById("refresh");
const tabs = [...document.querySelectorAll(".tab")];
const RENDER = { board: views.leaderboard, decisions: views.decisions, lessons: views.lessons, system: views.system };

const setAsOf = (iso) => { asof.textContent = iso ? `as of ${day(iso)}` : ""; };

async function show(name, { animate = true } = {}) {
  if (!RENDER[name]) name = "board";
  tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  if (!configured) {
    view.innerHTML = `<p class="empty">Not connected yet. The dashboard workflow fills in the Supabase address and public key.</p>`;
    return;
  }
  refresh.classList.add("spin");
  try {
    await RENDER[name](view, setAsOf);
  } catch (e) {
    view.innerHTML = `<p class="empty">Couldn't load data.<br><span class="note">${String(e.message).replace(/</g, "&lt;")}</span></p>`;
  } finally {
    refresh.classList.remove("spin");
  }
  if (animate) { view.classList.remove("view-enter"); void view.offsetWidth; view.classList.add("view-enter"); }
}

tabs.forEach((t) => t.addEventListener("click", () => { window.scrollTo(0, 0); show(t.dataset.tab); }));
refresh.addEventListener("click", () => show(location.hash.slice(1), { animate: false }));
document.addEventListener("visibilitychange", () => { if (!document.hidden) show(location.hash.slice(1), { animate: false }); });
window.addEventListener("hashchange", () => show(location.hash.slice(1)));
show(location.hash.slice(1));

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
