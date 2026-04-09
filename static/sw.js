// sw.js — Bolts11 PWA service worker
// Strategy:
//   - Precache the PWA shell + manifest + icons on install
//   - Static assets (CSS/JS/images): cache-first, network fallback
//   - API calls (/api/*, /doc/*): network-first, cache fallback
//   - Navigation requests: network-first with cached shell as offline fallback

const CACHE_VERSION = 'bolts11-v1';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const DYNAMIC_CACHE = `${CACHE_VERSION}-dynamic`;

const PRECACHE_URLS = [
  '/pwa/',
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

// Install — precache the shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(cache => {
      return cache.addAll(PRECACHE_URLS).catch(err => {
        // Don't fail install if some assets aren't ready yet
        console.warn('[sw] precache partial failure:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// Activate — clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => !key.startsWith(CACHE_VERSION))
          .map(key => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

// Fetch — route by request type
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);

  // Skip non-GET requests entirely
  if (request.method !== 'GET') return;

  // Skip cross-origin requests
  if (url.origin !== self.location.origin) return;

  // API + document routes — network first, cache as fallback
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/doc/')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // Static assets — cache first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Navigation requests — network first with PWA shell fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/pwa/'))
    );
    return;
  }

  // Everything else — try cache then network
  event.respondWith(
    caches.match(request).then(cached => cached || fetch(request))
  );
});

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(DYNAMIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw err;
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(STATIC_CACHE);
    cache.put(request, response.clone());
  }
  return response;
}
