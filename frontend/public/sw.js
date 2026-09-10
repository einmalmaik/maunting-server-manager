// Service Worker for Maunting Service Manager PWA

// CACHE_NAME muss bei jedem Release erhoeht werden, in dem sich statische
// Assets aendern (neue JS-Bundles, neue Icons, ...). Sonst liefert der SW
// nach einem Deploy die alten Bundles aus dem Cache und der Browser sieht
// den neuen Code nicht.
const CACHE_NAME = 'msm-v8';
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

  const url = new URL(event.request.url);
  // Kartenkacheln, Satellitenbilder und andere fremde Medien gehören nicht in
  // den PWA-Cache. Das Cache-Matching für viele kurzlebige Tile-URLs blockiert
  // die Gestensteuerung und kann abgebrochene externe Requests erzeugen.
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
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

// ── WebPush / FCM Push-Benachrichtigungen ──
// Implementiert strikte Foreground-Prüfung und Zero-Knowledge Privacy:
// 1. Bei aktiver WebSocket-/Browser-Verbindung im Vordergrund (focused)
//    werden keine doppelten OS-Pushes ausgelöst.
// 2. Bei E2EE- oder Privatsphäre-Filtern werden NIEMALS Klartextinhalte angezeigt.
self.addEventListener('push', (event) => {
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // 1. Foreground-Prüfung: Ist ein Fenster / Tab aktuell im Vordergrund aktiv?
      const hasFocusedClient = clientList.some((client) => client.focused);
      if (hasFocusedClient) {
        // Aktive Verbindung im Vordergrund -> keinen doppelten OS-Push auslösen!
        return;
      }

      // 2. Payload parsen
      let data = {};
      if (event.data) {
        try {
          data = event.data.json();
        } catch {
          data = { title: 'Maunting Server Manager', body: event.data.text() };
        }
      }

      // Steuersignale (Quittungen etc.) triggern keine OS-Pushes
      if (data.is_control || data.control_type === 'read_receipt' || data.control_type === 'delivery_receipt') {
        return;
      }

      // Outgoing Echo Prevention: Sender darf niemals einen OS-Push für eigene Aktionen erhalten
      const senderId = data.sender_user_id ?? data.sender_id;
      const targetId = data.target_user_id ?? data.recipient_id;
      if (senderId != null && targetId != null) {
        const sStr = String(senderId).trim();
        const tStr = String(targetId).trim();
        if (sStr === tStr) return;
        const sNum = Number(sStr);
        const tNum = Number(tStr);
        if (!isNaN(sNum) && !isNaN(tNum) && sNum === tNum) return;
      }

      // 3. Privacy-Schutz: Keine Klartextnachrichten leaken, wenn E2EE oder Privatsphäre aktiv ist
      const isE2ee = Boolean(data.is_e2ee || data.e2ee || data.privacy_filtered);
      let title = data.title || 'Neue Benachrichtigung';
      let body = 'Neue Benachrichtigung erhalten.';

      if (isE2ee) {
        // Zero-Knowledge: Niemals Klartext-Inhalt oder sensible Daten in OS-Benachrichtigung anzeigen
        const chatName = data.chat_name || data.sender_name;
        if (chatName) {
          title = data.is_group ? `Neue Nachricht in „${chatName}“` : `Neue Nachricht: ${chatName}`;
        } else {
          title = 'Neue Nachricht';
        }
        body = data.is_group ? 'Neue Nachricht in der Gruppe.' : 'Du hast eine neue verschlüsselte Nachricht erhalten.';
      } else if (data.body) {
        body = data.body;
      }


      return self.registration.showNotification(title, {
        body,
        icon: '/favicon.ico',
        badge: '/favicon.ico',
        tag: data.tag || 'msm-push-alert',
        renotify: true,
        data: {
          url: data.url || '/messenger',
        },
      });
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || '/messenger';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // Existierendes Fenster in den Vordergrund bringen
      for (const client of clientList) {
        if ('focus' in client && typeof client.focus === 'function') {
          return client.focus();
        }
      }
      // Falls kein Fenster offen ist, neues Fenster öffnen
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    }),
  );
});

