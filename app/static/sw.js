// Yandex2Kindle Service Worker
const CACHE_NAME = 'y2k-cache-static-v6';
const STATIC_ASSETS = [
  '/static/app.css',
  '/static/app.js',
  '/static/theme.js',
  '/static/workspace.css?v=6',
  '/static/favicon.svg',
  '/static/reader.css',
  '/static/reader.js',
  '/static/foliate/view.js',
  '/static/foliate/epub.js',
  '/static/foliate/paginator.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('Some static assets failed to pre-cache:', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Dynamic API requests or POST requests: bypass cache
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/') || url.pathname.startsWith('/syncs/')) {
    return;
  }

  // Static assets: cache-first with network fallback
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        return cached || fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Personal pages and files are never cached across account changes.
});
