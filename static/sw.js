// OfferWise AI — Service Worker v5.74.95
// Caches static assets for fast loading. Analysis calls always go to network.

const CACHE_NAME = 'offerwise-v5';

// Assets to cache on install (shell)
const SHELL_ASSETS = [
  '/app',
  '/static/app.html',
  '/static/manifest.json',
];

// Never cache these — always fresh from network
const NETWORK_ONLY = [
  '/api/',
  '/webhook/',
  '/login',
  '/logout',
  '/checkout',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SHELL_ASSETS).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Always network for API calls and auth
  if (NETWORK_ONLY.some(p => url.pathname.startsWith(p))) {
    return; // browser default
  }

  // Network-first for HTML pages (always fresh)
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .catch(() => caches.match('/app'))
    );
    return;
  }

  // Cache-first for static assets (JS, CSS, fonts, images)
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }
});
