/* Listener service worker (ADR-036, rev 2).
   This is a LAN/tailnet dashboard that must always show live data, so the SW must
   never serve a stale page or mask a down server.
   - Static assets (htmx, icons, manifest): cache-first — for install + fast loads.
   - Everything else (all HTML / dynamic GETs, every POST): NOT intercepted, so the
     browser goes straight to the network. If the homelab is down you get an honest
     connection error, never a fake cached dashboard. */
const CACHE = 'listener-v2';
const ASSETS = ['/static/htmx.min.js', '/static/icon.svg', '/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
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
  if (req.method !== 'GET') return;                 // POSTs (dismiss, etc.) → network
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const isAsset = url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest';
  if (!isAsset) return;                             // all HTML/dynamic → straight to network

  e.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
      return res;
    }))
  );
});
