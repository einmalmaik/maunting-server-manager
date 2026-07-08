# MSM v1.7.9 — Dateimanager-Upload-Fix + Panel-Backups Chunk-Fix

Release mit generischem Fix gegen doppelte Datei-Uploads im Dateimanager sowie
Behebung kaputter Lazy-Route-Ladung (z. B. Panel-Backups) nach Frontend-Deploys.

## Highlights

### Dateimanager: keine doppelten Uploads mehr (generisch)
Beim Drag & Drop von Dateien auf einen **Ordner** im Dateibaum konnte dieselbe
Datei zweimal hochgeladen werden (Event-Bubbling zum Baum-Panel + fehlende
In-Flight-Deduplizierung).

**Behoben:**
- Drop auf Ordnerzeile lädt nur in den Zielordner; interne Moves bubblen nicht
  mehr als zweiter Upload.
- Parallele Uploads derselben Datei ins gleiche Ziel werden abgewiesen
  (`uploadDestinationKey` + Toast).

### Panel-Backups / Lazy-Chunks nach Deploy
Nach einem Panel-Update konnten Routen wie **Panel-Backups** mit
`Failed to fetch dynamically imported module` und MIME-Fehler
(`text/html` statt JavaScript) abbrechen — typisch wenn `index.html` neue
Hash-Chunks referenziert, der Browser aber alte Chunks oder SPA-Fallback lädt.

**Root Cause:**
1. **Service Worker:** Cache-First für `/assets/*` hielt veraltete JS-Bundles.
2. **StaticFiles `html=True`:** Fehlende Chunk-URLs lieferten `index.html`
   statt 404.

**Fix:**
- PWA Service Worker: **Network-First** für `/assets/*`, `CACHE_NAME` → `msm-v7`.
- FastAPI: separates Mount `/assets` mit `html=False`, SPA-Fallback nur für
  Nicht-Asset-Pfade.

Die **401**-Meldungen in der Konsole bei kurzem Panel-Ausfall (503) sind
Session/Token abgelaufen — nach Update einmal neu einloggen oder Hard-Reload.
`tabs:outgoing.message.ready` stammt von einer Browser-Extension, nicht von MSM.

### Recovery App v0.2.0 (unverändert, weiter im Release)
Die **MSM Backup Recovery** Desktop-App (Entschlüsselung/Restore von Panel- und
Server-Backups) ist in diesem Release **inhaltlich unverändert** (weiter v0.2.0).
Die bekannten Builds (AppImage, `.deb`, `.rpm`, Windows `.exe`/`.msi`) werden
wie bei v1.7.8 erneut am GitHub-Release angehängt.

## Geänderte Dateien

### Frontend
- `frontend/src/pages/FileManager.tsx`: Upload-Dedup, Drop-Bubbling-Fix.
- `frontend/src/components/server/fileHelpers.ts`: `uploadDestinationKey`.
- `frontend/src/components/server/fileHelpers.test.ts`: Tests.
- `frontend/src/locales/de.json`, `en.json`: `uploadAlreadyRunning`.
- `frontend/public/sw.js`: Network-First für Assets, `msm-v7`.
- `frontend/package.json` + `package-lock.json`: Version `1.7.9`.

### Backend
- `backend/main.py`: Version `1.7.9`, getrenntes `/assets`-Static-Mount.

## Upgrade

> [!IMPORTANT]
> Nach dem Update Frontend neu bauen bzw. `update.sh` nutzen, damit `dist/` und
> `sw.js` zum Tag passen. Einmal **Hard-Reload** (oder PWA neu öffnen), damit der
> neue Service Worker aktiv wird.

```bash
sudo bash update.sh --force
```

— Maunting Studios