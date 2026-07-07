# MSM v1.7.3 — Service Worker Fetch Hotfix v2

Hotfix-Release zur Behebung eines unvollständig abgefangenen Promise-Fehlers im PWA Service Worker.

## Highlights

### Service Worker: Fehlerbehandlung bei Fetch-Ausfällen (Direkt-Catch)
Es wurde ein weitergehender Fehler behoben, bei dem Netzwerk-Verbindungsfehler (z. B. bei Offline-Modus oder Verbindungsstörungen) zu einer unbehandten Promise-Ablehnung führten.

**Behobene Details:**
- **Direktes `.catch()` auf `fetch()`:** Der Netzwerk-Fetch innerhalb der Cache-First-Strategie wird nun direkt an der Quelle abgefangen: `fetch(event.request).catch(...)`. Dadurch wird sichergestellt, dass die Promise-Ablehnung direkt in ein aufgelöstes Response-Objekt (`503 Offline`) umgewandelt wird und die Browser-Engine unter keinen Umständen mehr eine "Uncaught (in promise)"-Meldung ausgibt.
- **Cache-Name auf `msm-v6` erhöht:** Der Name des Caches wurde auf `msm-v6` angehoben, um eine Aktualisierung des Service Workers auf allen Endgeräten zu erzwingen.

## Geänderte Bereiche

### Frontend
- `frontend/public/sw.js`: Fehlerbehandlung direkt am Fetch abgefangen, Bump `CACHE_NAME` auf `msm-v6`.
- `frontend/package.json`: Version `1.7.2` -> `1.7.3`.

### Backend
- `backend/main.py`: App-Version und `/api/version`-Antwort auf `1.7.3` aktualisiert.

## Upgrade-Hinweise

> [!IMPORTANT]
> Da der Frontend-Build auf Produktions-Servern nicht im Git-Repository versioniert ist, muss nach dem `git pull` der Frontend-Build neu erstellt oder das Update-Script genutzt werden!

```bash
# Empfohlen: Update über das offizielle Update-Script (baut Frontend und startet Dienste neu)
sudo bash update.sh --force
```

Alternativ manuell:
```bash
# 1. Pull
cd /opt/msm && git pull

# 2. Frontend manuell bauen
cd frontend && npm install && npm run build

# 3. Panel restarten
sudo systemctl restart msm-panel
```

— Maunting Studios
