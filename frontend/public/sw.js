// Service Worker for Maunting Server Manager PWA

// CACHE_NAME muss bei jedem Release erhoeht werden, in dem sich statische
// Assets aendern (neue JS-Bundles, neue Icons, ...). Sonst liefert der SW
// nach einem Deploy die alten Bundles aus dem Cache und der Browser sieht
// den neuen Code nicht.
const CACHE_NAME = 'msm-v7';
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
  '/favicon.ico',
  '/logo.png',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    Promise.all([
      caches.keys().then((cacheNames) =>
        Promise.all(
          cacheNames.map((cacheName) => {
            if (cacheName !== CACHE_NAME) {
              return caches.delete(cacheName);
            }
          }),
        ),
      ),
      self.clients.claim(),
    ]),
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  if (event.request.url.includes('/api/')) return;

  const url = new URL(event.request.url);
  const isHashedAsset = url.pathname.startsWith('/assets/');

  const isHtmlRequest =
    event.request.mode === 'navigate' ||
    event.request.headers.get('accept')?.includes('text/html');

  if (isHashedAsset) {
    // Network-First fuer Vite-Hashes: nach Deploy referenziert index.html neue
    // Chunk-Namen; Cache-First lieferte alte Chunks oder HTML-Fallback als JS.
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        })
        .catch(() =>
          caches.match(event.request).then(
            (cached) =>
              cached ||
              new Response('Asset offline', {
                status: 503,
                statusText: 'Offline',
                headers: { 'Content-Type': 'text/plain' },
              }),
          ),
        ),
    );
    return;
  }

  if (isHtmlRequest) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        })
        .catch(() =>
          caches
            .match(event.request)
            .then((response) => response || caches.match('/'))
            .then(
              (response) =>
                response ||
                new Response('Offline', {
                  status: 503,
                  statusText: 'Offline',
                  headers: { 'Content-Type': 'text/plain' },
                }),
            ),
        ),
    );
  } else {
    event.respondWith(
      caches.match(event.request).then(
        (response) =>
          response ||
          fetch(event.request).catch(
            () =>
              new Response('Offline', {
                status: 503,
                statusText: 'Offline',
                headers: { 'Content-Type': 'text/plain' },
              }),
          ),
      ),
    );
  }
});