# MSM v1.7.2 — Service Worker Fetch Hotfix

Hotfix-Release zur Behebung eines unvollständig abgefangenen Promise-Fehlers im PWA Service Worker.

## Highlights

### Service Worker: Fehlerbehandlung bei Fetch-Ausfällen
Es wurde ein Fehler behoben, bei dem Netzwerk-Verbindungsfehler (z. B. im Offline-Modus oder bei Verbindungsstörungen) zu einer unbehandelten Promise-Ablehnung (`Uncaught (in promise) TypeError: Failed to fetch`) führten.

**Behobene Details:**
- **Cache-First `.catch()` Integration:** In der Cache-First-Strategie für statische Assets (`sw.js`) wurde ein `.catch()` Block hinzugefügt. Schlägt ein Netzwerk-Fetch für ein nicht gecachtes Asset fehl, wird nun eine saubere `503 Offline`-Response zurückgegeben.
- **Logikfehler in HTML-Fallback gelöst:** Die Fehlerbehandlung der Network-First-Strategie nutzte die JavaScript-Verknüpfung `caches.match(event.request) || caches.match('/')`. Da `caches.match` ein Promise-Objekt zurückgibt (welches immer truthy ist), wurde der `/`-Fallback bei Cache-Misses nie aufgerufen. Dies wurde durch eine korrekte Promise-Verkettung behoben.
- **Cache-Aktualisierung (msm-v5):** Der Cache-Name wurde auf `msm-v5` gebumpt, um ein erneutes Einlesen der lokalen Browser-Caches auf Client-Geräten zu erzwingen.

## Geänderte Bereiche

### Frontend
- `frontend/public/sw.js`: Fehlerbehandlung bei fehlgeschlagenen Fetches, Behebung der Promise-Logik im HTML-Fallback, Bump `CACHE_NAME` auf `msm-v5`.
- `frontend/package.json`: Version `1.7.1` -> `1.7.2`.

### Backend
- `backend/main.py`: App-Version und `/api/version`-Antwort auf `1.7.2` aktualisiert.

## Upgrade-Hinweise

```bash
# 1. Pull
cd /opt/msm && git pull

# 2. Panel restarten
sudo systemctl restart msm-panel
```

— Maunting Studios
