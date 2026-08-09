/* Service worker: keep the app shell available with no network.
 *
 * Only the shell is cached. API responses deliberately are not — a stale queue
 * or a stale severity score is worse than an honest "cannot reach the server",
 * and the app already has its own offline path for the one thing that must
 * survive a blackout: filing a report.
 */

const CACHE = 'resq-shell-v2';

const SHELL = [
  './',
  './index.html',
  './control.html',
  './manifest.webmanifest',
  './css/base.css',
  './css/citizen.css',
  './css/control.css',
  './js/api.js',
  './js/store.js',
  './js/ui.js',
  './js/geo.js',
  './js/citizen.js',
  './js/i18n.js',
  './js/profile.js',
  './js/signals.js',
  './js/voice.js',
  './js/control.js',
  './assets/leaflet/leaflet.css',
  './assets/leaflet/leaflet.js',
  './icons/icon.svg',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      // addAll fails the whole install if any single file 404s; tolerate gaps.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Never serve API or map tiles from cache.
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') return;
  if (url.hostname.endsWith('tile.openstreetmap.org')) return;

  event.respondWith(
    caches.match(request).then((hit) => {
      if (hit) {
        // Refresh in the background so the next load is current.
        fetch(request)
          .then((response) => {
            if (response.ok) caches.open(CACHE).then((cache) => cache.put(request, response));
          })
          .catch(() => {});
        return hit;
      }
      return fetch(request).catch(() => caches.match('./index.html'));
    }),
  );
});
