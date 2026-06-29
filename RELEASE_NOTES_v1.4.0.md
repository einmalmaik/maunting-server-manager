## Ausgehende Webhooks (MSM → Drittsysteme)

Mit v1.4.0 kann MSM aktiv Daten an externe Systeme schicken — etwa einen
Discord-Bot, der den Server-Status und die Spielerzahl anzeigt, oder einen
internen Monitor (Uptime-Kuma, Grafana). Das Gegenstück zu bisherigen
internen Polling-Mechanismen, aber jetzt offiziell vom Panel verwaltet.

### Neue Features

- **Outgoing-Webhooks pro Server**
  - Neuer Tab **„Webhooks“** auf der Server-Detail-Seite (`OutgoingWebhooksPanel.tsx`).
  - Beliebig viele Subscriptions pro Server (Discord-Bot, internen Monitor, …).
  - URL wird vom Empfänger vorgegeben (z. B. der Discord-Bot generiert eine
    UUID-URL; der User trägt sie in MSM ein).
  - Auth gegenüber dem Empfänger über `X-Webhook-Secret`-Header.
- **Secret-Handling nach AGENTS.md**
  - Beim Anlegen oder Rotieren erzeugt das Panel einen URL-sicheren 32-Byte-Token
    (`secrets.token_urlsafe(32)`) und gibt ihn genau **einmal** an den Aufrufer zurück.
  - In der DB liegt nur `SHA-256(secret)`. Klartext lebt nur in einem
    prozess-lokalen In-Memory-Store und geht beim Neustart verloren (sicherer Default,
    vermeidet eigene symmetrische Crypto laut Security.md §4).
  - Ein kurzer Hint (letzte 4 Zeichen) bleibt sichtbar — reine UX-Erinnerung.
- **Versand mit Backoff und Lifecycle-Hook**
  - `services/outbound_webhook_service.py` feuert beim **Status-Wechsel**
    eines Servers automatisch ein `status_change`-Event
    (Hook in `services/server_lifecycle_service._set_status`).
  - Fire-and-forget: HTTP-Request blockiert keine UI-Interaktion.
  - 5xx-Fehler → automatischer Retry mit 1 s / 4 s / 16 s Backoff (max. 4 Versuche).
  - 4xx-Fehler → kein Retry (Client-Fehler, würde nur scheitern).
  - Connection-Errors → ebenfalls Retry.
- **Live-Feed im Panel**
  - `webhook_deliveries`-Tabelle hält alle Zustell-Versuche fest (status,
    response_code, payload-Hash, attempt, sent_at).
  - Tab zeigt die letzten 20 Events mit Status-Badge (✓ / ✗ / …).
  - Retention: 200 Events / Server und 7 Tage.
- **Manueller Test-Button**
  - Sendet sofort ein synthetisches Event ohne auf echte Lifecycle-Änderungen zu warten
    — ideal, um die Empfänger-URL beim Setup zu verifizieren.
- **Mehrsprachigkeit**
  - 11 Locale-Dateien (`en`, `de`, plus 9 weitere) gepflegt; deutsche Texte
    mit korrekten Umlauten und `ß` gemäß `AGENTS.md` §10.
  - DE + EN voll übersetzt (41 Strings); weitere Sprachen erhalten Kern-Strings,
    Rest via `defaultValue`-Fallback.

### Sicherheits- und KISS-Invarianten

- ✅ **kein** Klartext-Secret in Logs (Test `test_plaintext_secret_never_logged_in_app`).
- ✅ **kein** Klartext-Secret im `GET /webhooks`-Response (Test
  `test_get_config_does_not_leak_plaintext_secret`).
- ✅ **alle** state-changing Endpoints (`POST`, `PATCH`, `DELETE`) durch
  `verify_csrf`-Dependency geschützt.
- ✅ RBAC via `require_server_permission("server.update", …)`.
- ✅ Payload-Größe auf 16 KiB begrenzt; größere Bodies → `413 Payload too large`.
- ✅ Keine Folgengesteuerte Redirects (`follow_redirects=False`) gegen
  versehentliche SSRF-Ketten via 30x-Redirect zu internen Hosts.
- ✅ Domain-Whitelist der URL (`http(s)://`-Prefix).
- ✅ Kein neues Secret-Material in `webhooks_outbound.py` direkt: alle Krypto-
  Helper kommen aus `services/webhook_event_service`/`hashlib`/`hmac`/
  `secrets` (Standardbibliothek — keine neue Supply-Chain).

### Architektur-Schnitt

- **UI**: `OutgoingWebhooksPanel.tsx` (ServerDetail-Tab)
- **Service**: `services/outbound_webhook_service.py` — pure async dispatch,
  bewusst klein gehalten (~300 LOC) — bewusst KEIN Manager/Pipeline-Klasse
  (siehe `architecture.md` §5).
- **Router**: `routers/webhooks_outbound.py` (5 Endpoints).
- **Storage**: 2 neue Tabellen (`webhook_subscriptions`, `webhook_deliveries`)
  via `Base.metadata.create_all`; idempotente Migration läuft in `lifespan`.
- **Trigger**: minimal-invasiver Hook in `services/server_lifecycle_service._set_status`
  (fire-and-forget, kein Cloudflare-Worker, kein Cron — KISS).

### Geänderte Dateien

```
backend/main.py
backend/models/__init__.py
backend/models/webhook_subscription.py
backend/models/webhook_delivery.py
backend/routers/__init__.py
backend/routers/webhooks_outbound.py
backend/services/outbound_webhook_service.py
backend/services/outbound_webhook_secret_store.py
backend/services/server_lifecycle_service.py
backend/tests/test_webhooks_outbound.py
frontend/src/components/server/OutgoingWebhooksPanel.tsx
frontend/src/pages/ServerDetail.tsx
frontend/src/locales/{en,de,ar,bn,es,fr,hi,id,pt,ru,zh}.json
```

### Migration / Upgrade-Hinweise

- **Kein** manueller Schritt erforderlich. Tabellen werden beim ersten Start
  nach dem Update automatisch angelegt.
- Bestehende Serververhalten sind unverändert (Webhook ist opt-in).
- Klartext-Secrets gehen beim Restart verloren — bewusste Entscheidung.
  Nach Panel-Neustart müssen User einmalig neue Secrets generieren
  (`POST /webhooks/{id}/rotate`).

### Tests

- 14 neue Backend-Tests (`tests/test_webhooks_outbound.py`)
- End-to-End gegen das Live-Panel verifiziert:
  - ✅ Subscription anlegen → Secret einmalig zurück
  - ✅ Test-Send → HTTP 200, `X-Webhook-Secret` korrekt mitgesendet
  - ✅ Rotieren → alter Token invalidiert, neuer Token aktiv
  - ✅ 5xx-Test → 3 Retries mit Backoff, dann `status=failed`
