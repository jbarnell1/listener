/* Listener service worker (ADR-036).
   - App shell (home, htmx, icons, manifest) precached for instant open + offline.
   - Navigations: network-first (dashboard data stays fresh), fall back to the last
     cached page, then the home shell, when the homelab is unreachable.
   - Static assets: cache-first.
   - Never touches /ingest, /telemetry, /healthz, or the SSE assistant stream. */
const CACHE = 'listener-v1';
const SHELL = ['/', '/static/htmx.min.js', '/static/icon.svg', '/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // Live endpoints must always hit the network (never cached).
  if (/^\/(assistant|ingest|telemetry|healthz)\b/.test(url.pathname)) return;

  const isStatic = url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest';
  if (isStatic) {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      }))
    );
    return;
  }

  // Navigations + dynamic GETs: network-first with a cached fallback.
  e.respondWith(
    fetch(req).then((res) => {
      if (req.mode === 'navigate' && res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || caches.match('/')))
  );
});
