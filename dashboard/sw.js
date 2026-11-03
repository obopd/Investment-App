// Bump CACHE whenever a file in ASSETS changes, so installed copies update.
const CACHE = "arena-v4";
const ASSETS = [
  "./", "index.html", "styles.css", "manifest.webmanifest", "config.js",
  "js/app.js", "js/api.js", "js/views.js", "js/chart.js", "js/format.js", "js/sheet.js",
  "fonts/fraunces-latin-wght-normal.woff2", "icons/icon.svg", "icons/icon-180.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS.map((u) => new Request(u, { cache: "reload" })))));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

// App files: network first (updates show up), cache when offline. Data is never cached here.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  event.respondWith(
    fetch(event.request).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(event.request, copy));
      return res;
    }).catch(() => caches.match(event.request)),
  );
});
