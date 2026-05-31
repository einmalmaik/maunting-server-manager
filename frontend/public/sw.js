// Service Worker for Maunting Server Manager PWA

const CACHE_NAME = 'msm-v1';
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
  '/favicon.ico',
  '/logo.png',
  // Add other static assets as needed
];

// Install event - cache static assets and skip waiting to activate immediately
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
  );
});

// Activate event - clean up old caches and claim clients immediately
self.addEventListener('activate', (event) => {
  event.waitUntil(
    Promise.all([
      caches.keys().then((cacheNames) => {
        return Promise.all(
          cacheNames.map((cacheName) => {
            if (cacheName !== CACHE_NAME) {
              return caches.delete(cacheName);
            }
          })
        );
      }),
      self.clients.claim()
    ])
  );
});

// Fetch event - Network-First for HTML/navigation, Cache-First for others
self.addEventListener('fetch', (event) => {
  // Skip non-GET requests
  if (event.request.method !== 'GET') return;
  
  // Skip API calls - always try network
  if (event.request.url.includes('/api/')) return;
  
  const isHtmlRequest = event.request.mode === 'navigate' || 
                        event.request.headers.get('accept')?.includes('text/html');
  
  if (isHtmlRequest) {
    // Network-First strategy: always fetch the latest HTML from the network first
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // If response is valid, update the cache with the new HTML
          if (response && response.status === 200) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        })
        .catch(() => {
          // Fallback to cache if network fails (offline)
          return caches.match(event.request) || caches.match('/');
        })
    );
  } else {
    // Cache-First strategy for static assets
    event.respondWith(
      caches.match(event.request)
        .then((response) => {
          // Return cached version if found, otherwise fetch from network
          return response || fetch(event.request);
        })
    );
  }
});
