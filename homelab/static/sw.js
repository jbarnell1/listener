/* Listener service worker (ADR-036, rev 3).
   This is a LAN/tailnet dashboard that must always show live data, so the SW must
   never serve a stale page or mask a down server.
   - Static assets (htmx, icons, manifest): cache-first — for install + fast loads.
   - Everything else (all HTML / dynamic GETs, every POST): NOT intercepted, so the
     browser goes straight to the network. If the homelab is down you get an honest
     connection error, never a fake cached dashboard.
   BASE: the SW is served at <base>/sw.js (base is "" at root, "/listener" when the app is
   mounted under a path so it can share an origin with another PWA). Everything is scoped to
   BASE so it works in both layouts. */
const BASE = self.location.pathname.replace(/\/sw\.js$/, '');
const CACHE = 'listener-v4';
const ASSETS = [BASE + '/static/htmx.min.js', BASE + '/static/icon.svg', BASE + '/manifest.webmanifest'];

function scoped(url) {
  // Keep server-sent, root-relative notification URLs inside this app's base.
  if (url && url.startsWith('/') && !url.startsWith('//') && !url.startsWith(BASE + '/') && url !== BASE) {
    return BASE + url;
  }
  return url;
}

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

// --- Web Push (ADR-046): show device-health alerts on the phone ---
self.addEventListener('push', (e) => {
  let d = { title: 'Listener', body: '', url: BASE + '/' };
  try { d = Object.assign(d, e.data.json()); } catch (_) { if (e.data) d.body = e.data.text(); }
  e.waitUntil(self.registration.showNotification(d.title, {
    body: d.body, icon: BASE + '/static/icon.svg', badge: BASE + '/static/icon.svg',
    data: { url: scoped(d.url) }, tag: 'listener', renotify: true,
  }));
});
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = scoped((e.notification.data && e.notification.data.url) || (BASE + '/'));
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((cs) => {
    for (const c of cs) { if ('focus' in c) return c.focus(); }
    return clients.openWindow(url);
  }));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // POSTs (dismiss, etc.) → network
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const isAsset = url.pathname.startsWith(BASE + '/static/') || url.pathname === BASE + '/manifest.webmanifest';
  if (!isAsset) return;                             // all HTML/dynamic → straight to network

  e.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
      return res;
    }))
  );
});
