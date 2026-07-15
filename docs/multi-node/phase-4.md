# Phase 4: Frontend entkoppeln (Vercel-Ready)

Dieses Dokument spezifiziert die Entkopplung des React-Frontends vom FastAPI-Backend, um ein unabhängiges Hosting des Frontends (z.B. auf Vercel) über CDNs zu ermöglichen.

---

## 1. Frontend-Anpassungen

### 1.1 Dynamische API- und WebSocket-URLs
Das Frontend darf keine relativen Pfade mehr annehmen, da die API-Domain sich von der Frontend-Domain unterscheidet.
- Pfad: `frontend/src/services/api.js` (und überall dort, wo Verbindungen aufgebaut werden).
- Konfiguration über Vite-Umgebungsvariablen:
  ```javascript
  const API_BASE = import.meta.env.VITE_API_URL || window.location.origin;
  const WS_BASE = import.meta.env.VITE_WS_URL || 
      API_BASE.replace('https://', 'wss://').replace('http://', 'ws://');
  ```

### 1.2 SPA Routing auf Vercel (`vercel.json`)
Da React-Router clientseitiges Routing verwendet, müssen Anfragen, die keine statischen Dateien betreffen, an `/index.html` weitergeleitet werden.
Erstelle `frontend/vercel.json`:
```json
{
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

---

## 2. Backend-Anpassungen

### 2.1 CORS-Policy erweitern
In `backend/config.py` und `backend/main.py` muss die CORS-Middleware so konfiguriert werden, dass sie die externe Domain des gehosteten Frontends akzeptiert (z.B. `https://maunting-panel.vercel.app`).
- Die Domain wird aus einer neuen Env-Variable `MSM_CORS_ALLOWED_ORIGINS` (Komma-separiert) ausgelesen.

### 2.2 Cross-Domain Cookie-Richtlinien
Da das Panel Cookie-basierte Authentifizierung nutzt, müssen Cookies für Cross-Domain-Szenarien angepasst werden:
- Pfad: `backend/cookies.py`
- Für Cross-Domain muss das Cookie mit `samesite="none"` und `secure=True` (erfordert HTTPS) gesendet werden.
- Bei lokaler Entwicklung ohne HTTPS verbleibt das Cookie auf `samesite="lax"`.
- *Sicherheits-Invariante*: Cookies müssen weiterhin das `httponly=True` Flag tragen, um XSS-Angriffe zu verhindern.

### 2.3 Deaktivierung des lokalen Static-Servings
Bisher mountet das Backend das Frontend über `StaticFiles(directory="/opt/msm/frontend/dist")`.
- Führe eine Env-Variable `MSM_SERVE_FRONTEND` (Default: `true` für Abwärtskompatibilität) ein.
- Wenn `MSM_SERVE_FRONTEND=false`, wird das statische Dateiserving im Backend komplett übersprungen. Dies spart Ressourcen auf dem API-Server.

### 2.4 Content-Security-Policy (CSP)
Passe das CSP-Middleware in `backend/main.py` an. `connect-src` muss Zugriffe vom externen Frontend erlauben.

---

## 3. Test- und Verifizierungsschritte

1. **Lokaler Cross-Domain-Test**:
   - Starte das Backend auf `http://127.0.0.1:8080`.
   - Starte das Frontend via Vite auf `http://localhost:5173`.
   - Stelle sicher, dass Cookies über Kreuz korrekt übertragen werden (Credentials-Flag im Axios-Client und CORS im Backend aktiv).
   - Teste, ob Login, API-Anfragen und Konsolen-WebSockets fehlerfrei über verschiedene Origins hinweg funktionieren.
