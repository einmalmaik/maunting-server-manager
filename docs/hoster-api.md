# MSM Hoster-API — Referenz für Shop-Anbindungen

Diese Datei ist die vollständige technische Referenz für Entwickler, die einen externen Shop an
Maunting Server Manager anbinden. Die Einrichtung im Panel und die betrieblichen Zusammenhänge
stehen in [self-hosting.md](self-hosting.md#hoster--und-shop-anbindung-optional-phase-6); hier geht
es ausschließlich um den Vertrag zwischen Shop und MSM.

Dieselben Inhalte sind im Panel unter **Dokumentation → Hoster-API** (`/docs/hoster-api`)
zweisprachig verfügbar.

**Ohne angelegte Integration existiert nichts aus diesem Dokument.** Ein Self-Hosted-Betrieb ohne
Shop hat keinen offenen Endpunkt, der ohne API-Key etwas tut.

---

## Inhalt

1. [Grundprinzip](#grundprinzip)
2. [Authentifizierung](#authentifizierung)
3. [Externe API](#externe-api)
4. [Zustandsmodell](#zustandsmodell)
5. [Fehler und Statuscodes](#fehler-und-statuscodes)
6. [Webhooks](#webhooks)
7. [Signatur nachrechnen](#signatur-nachrechnen)
8. [Ein-Klick-Handoff](#ein-klick-handoff)
9. [Panel-Verwaltung (Admin-Endpunkte)](#panel-verwaltung-admin-endpunkte)
10. [Betriebshinweise](#betriebshinweise)

---

## Grundprinzip

Der Shop sendet **keine Befehle**, sondern einen **gewünschten Zustand**. Statt „starte den Server"
meldet er „dieser Vertrag soll aktiv sein". Das ist der Kern der Idempotenz: ein Befehl, der wegen
eines Timeouts zweimal ankommt, wäre gefährlich — ein Zielzustand, der zweimal ankommt, ist
folgenlos.

Der Shop übermittelt bewusst **keine internen MSM-Details**: keine Node-ID, keine Portnummern, keine
Installationspfade. Er nennt eine Produktkennung; MSM löst daraus Blueprint, Ressourcen, Host und
Ports selbst auf.

Alles, was die Hoster-API auslöst, läuft durch dieselben Services wie ein Klick im Panel
(`server_provisioning_service`, `server_action_service`, `server_deletion_service`). Es gibt keinen
Pfad, der RBAC, Kapazitätsprüfung, Portvergabe oder Guardian umgeht.

---

## Authentifizierung

Alle Endpunkte unter `/api/hoster/v1/` erwarten den Integrations-API-Key im Header:

```
X-MSM-Hoster-Key: <api-key>
```

Es gibt **keinen Cookie-Pfad**. Eine Browser-Session kann diese Endpunkte nicht ansprechen, und
CORS ist für sie irrelevant — der Aufruf muss serverseitig aus dem Shop erfolgen. Ein Test aus der
Browserkonsole schlägt deshalb immer fehl; das ist Absicht, kein Konfigurationsfehler.

MSM speichert vom API-Key nur den SHA-256-Hash. Der Klartext erscheint genau einmal bei der
Erzeugung im Panel und ist danach nicht wieder abrufbar.

Der API-Key ist **gleichwertig zu den Rechten des Dienstbenutzers** der Integration — nicht mehr.
Die Sicherheitsgrenze ist dieser Benutzer und sein RBAC, nicht der Schlüssel.

| Situation | Antwort |
| --- | --- |
| Header fehlt | `401` |
| Key unbekannt oder Integration deaktiviert | `401` |

Beide Fälle liefern denselben Text, damit ein Angreifer nicht unterscheiden kann, ob ein Schlüssel
existiert.

Ein Schlüsseltest ohne Nebenwirkung:

```bash
curl -sS https://panel.example/api/hoster/v1/health \
  -H "X-MSM-Hoster-Key: $MSM_HOSTER_KEY"
```

```json
{"ok": true, "integration": "mein-shop"}
```

---

## Externe API

| Methode | Pfad | Zweck |
| --- | --- | --- |
| `GET` | `/api/hoster/v1/health` | API-Key prüfen, ohne etwas zu ändern |
| `PUT` | `/api/hoster/v1/services/{external_service_id}` | Gewünschten Vertragszustand setzen |
| `GET` | `/api/hoster/v1/services/{external_service_id}` | Tatsächlichen Zustand abfragen |
| `POST` | `/api/hoster/v1/handoffs` | Einmal-Link in das Panel des Kunden erzeugen |
| `GET` | `/api/hoster/handoff/{token}` | Einlösung durch den Browser des Kunden (kein API-Key) |

### `PUT /api/hoster/v1/services/{external_service_id}`

Setzt den gewünschten Zustand eines Vertrags. Der Aufruf legt den Vertrag beim ersten Mal an und
führt ihn danach fort.

`external_service_id` ist die Vertragskennung **aus dem Shop** — maximal 128 Zeichen. Zusammen mit
der Integration ist sie eindeutig und dient als Idempotenzschlüssel.

**Request-Body**

| Feld | Typ | Pflicht | Beschreibung |
| --- | --- | --- | --- |
| `desired_state` | `string` | ja | `active`, `suspended` oder `terminated` |
| `external_subject` | `string` (1–128) | ja | Kunden-ID **aus dem Shop**. Die feste Zuordnung läuft über diese ID, nicht über die E-Mail — sonst bräche eine E-Mail-Änderung des Kunden die Verknüpfung. |
| `product_key` | `string` (1–128) | bei Neuanlage | Shop-Produktkennung. MSM bildet sie auf Blueprint und Ressourcenpaket ab. Bei einem bestehenden Vertrag löst ein abweichender Wert einen Tarifwechsel aus. |
| `email` | `string` (≤255) | nein | Wird nur für die Erstanlage des Panel-Benutzers verwendet. MSM übernimmt **niemals** ein Shop-Passwort. |

**Beispiel**

```bash
curl -X PUT https://panel.example/api/hoster/v1/services/SVC-4711 \
  -H "X-MSM-Hoster-Key: $MSM_HOSTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "desired_state": "active",
        "external_subject": "CUST-1234",
        "product_key": "mc-8gb",
        "email": "kunde@example.com"
      }'
```

**Response-Body** (identisch für `PUT` und `GET`)

| Feld | Typ | Beschreibung |
| --- | --- | --- |
| `external_service_id` | `string` | Die Vertragskennung des Shops |
| `desired_state` | `string` | Zuletzt gemeldeter Zielzustand |
| `status` | `string` | Tatsächlicher Zustand — siehe [Zustandsmodell](#zustandsmodell) |
| `status_code` | `string \| null` | Stabiler Fehler- oder Hinweiscode, sonst `null` |
| `server_id` | `integer \| null` | Interne Server-ID, sobald einer existiert |
| `task_id` | `string \| null` | ID des laufenden Provisionierungs- oder Lifecycle-Vorgangs |
| `correlation_id` | `string` (UUID) | Klammert alle Audit-Einträge und Webhooks dieses Vertrags |
| `terminate_after` | `string \| null` | ISO-8601-Zeitpunkt, ab dem nach einer Kündigung gelöscht wird |
| `updated_at` | `string` | ISO-8601-Zeitpunkt der letzten Änderung |

```json
{
  "external_service_id": "SVC-4711",
  "desired_state": "active",
  "status": "ready",
  "status_code": null,
  "server_id": 42,
  "task_id": "0a3f1b7c-2d4e-4f60-9a11-8c5e2f7b6d33",
  "correlation_id": "6f6d9d1e-6b1e-4a51-9f0c-2b7a5d3e8c14",
  "terminate_after": null,
  "updated_at": "2026-08-08T09:22:10+00:00"
}
```

**Wiederholbarkeit.** Derselbe Aufruf darf beliebig oft gesendet werden. Ein Netzwerk-Retry erzeugt
keinen zweiten Server: die Provisionierung ist über `(Integration, Service)` an einen
Idempotency-Key gebunden. Auch zwei *gleichzeitige* Erstaufrufe führen zu genau einem Vertrag — der
Verlierer übernimmt den Gewinner.

### `GET /api/hoster/v1/services/{external_service_id}`

Fragt den tatsächlichen Zustand ab. Kein Body. Antwortet mit `404`, wenn der Vertrag nicht existiert.

Die Provisionierung läuft asynchron: nach `PUT` kann `status` noch `provisioning` sein. Der Shop
sollte entweder auf den Webhook `service.ready` warten oder in Abständen `GET` aufrufen — nicht in
einer engen Schleife.

### `POST /api/hoster/v1/handoffs`

Siehe [Ein-Klick-Handoff](#ein-klick-handoff).

---

## Zustandsmodell

`desired_state` ist das, was der Shop **will**. `status` ist das, was **ist**. Die beiden können
vorübergehend auseinanderlaufen — genau dafür gibt es zwei Felder.

**Zielzustände** (vom Shop gesetzt):

| `desired_state` | Bedeutung |
| --- | --- |
| `active` | Vertrag läuft. Server wird bei Bedarf erstellt, Kundenrechte werden gesetzt. |
| `suspended` | Zahlungssperre. Server wird gestoppt, Kundenrechte werden entzogen. **Der Panelaccount bleibt bestehen**, Daten bleiben erhalten, eine spätere Entsperrung ist möglich. |
| `terminated` | Kündigung. Server wird gestoppt und eine Frist gesetzt. **Es wird nichts sofort gelöscht.** |

**Tatsächliche Zustände** (von MSM gesetzt, jeder erzeugt einen Webhook `service.<status>`):

| `status` | Bedeutung |
| --- | --- |
| `pending` | Vertrag angelegt, technische Umsetzung noch nicht begonnen |
| `provisioning` | Server wird gerade erstellt und installiert |
| `ready` | Server existiert und ist dem Kunden zugewiesen |
| `suspended` | Server gestoppt, Kundenrechte entzogen, Daten erhalten |
| `terminating` | Gekündigt, Frist läuft — der Server existiert noch |
| `terminated` | Frist abgelaufen, Server gelöscht (`server_id` ist danach `null`) |
| `failed` | Ein Vorgang ist gescheitert. `status_code` nennt den Grund. |

Der Übergang `terminating → terminated` passiert **nicht** im API-Aufruf, sondern in einem
Wartungslauf, der jede Minute prüft, ob `terminate_after` erreicht ist. Der Löschpfad ist derselbe
wie im Panel.

Ein Tarifwechsel (`product_key` weicht ab) setzt `status_code` auf
`product_changed_manual_resize_required`. Die Produktzuordnung ändert sich sofort, die
Ressourcengrenzen eines **laufenden** Servers bleiben eine bewusste Operator-Aktion — sie erfordern
einen Neustart.

Trägt die Produktzuordnung eine `role_id`, vergibt `active` diese globale Rolle **zusätzlich** zu
den bestehenden Rollen des Kunden; `suspended` und `terminated` entziehen sie wieder — aber nur,
wenn kein anderer laufender Vertrag desselben Kunden dieselbe Rolle noch fordert, und niemals,
wenn es eine Systemrolle ist. Manuell vergebene Rollen bleiben unangetastet. Über globale Rollen
laufen unter anderem die KI-Kontingente; genau dafür ist das Feld gedacht: ein größerer Tarif bringt
mehr KI-Budget mit, ohne dass jemand von Hand Rollen pflegt.

Maßgeblich ist dabei, was ein Vertrag **tatsächlich vergeben hat**, nicht was gerade an seinem
Produkt steht. Daraus folgen drei Zusagen, auf die ein Shop sich verlassen kann:

- Ein **Tarifwechsel** tauscht die Rolle, er stapelt sie nicht: die Rolle des vorherigen Produkts
  wird entzogen, die des neuen vergeben — beides im selben Aufruf.
- Ändert oder entfernt der Betreiber die Rolle eines Produkts, während Verträge darauf laufen, wird
  bei der nächsten Zustandsänderung trotzdem die **ursprünglich vergebene** Rolle zurückgenommen.
- Ein Vertrag, dessen Aktivierung fehlgeschlagen ist (`status: "failed"`), trägt keine Rolle — auch
  dann nicht, wenn sein `desired_state` weiterhin `active` lautet.

Löscht der Betreiber ein Produkt, während ein Vertrag darauf läuft, behält der Kunde die bereits
vergebene Rolle bis zum Ende des Vertrags. Ein Aufräumen im Panel soll einem zahlenden Kunden nicht
das Kontingent nehmen.

---

## Fehler und Statuscodes

| HTTP | Wann |
| --- | --- |
| `200` | Erfolg (auch `PUT`; es gibt kein `201`) |
| `401` | API-Key fehlt, ist ungültig oder die Integration ist deaktiviert |
| `404` | Vertrag unbekannt (`GET`, Handoff) |
| `422` | Konfigurationsfehler — unbekanntes Produkt, deaktiviertes Produkt, fehlende Produktkennung bei Neuanlage, deaktivierter Dienstbenutzer, ungültiges Handoff-Ziel |
| `503` | Interner Fehler bei der Umsetzung |

**Wichtig:** Ein fachlicher Fehlschlag ist nicht nur ein HTTP-Fehler. Der Vertrag bleibt mit
`status: "failed"` und einem stabilen `status_code` abfragbar, damit der Shop nachträglich
herausfinden kann, was passiert ist.

Häufige `status_code`-Werte:

| Code | Bedeutung |
| --- | --- |
| `port_conflict` | Kein freier Portblock auf dem Zielhost |
| `node_not_found` | Das Produkt verweist auf eine Node, die es nicht gibt |
| `install_directory_exists` | Rückstand einer früheren Installation |
| `install_update_already_running` | Auf dem Server läuft bereits eine Installation |
| `hoster_configuration_error` | Produkt, Dienstbenutzer oder Identität sind nicht nutzbar |
| `hoster_role_escalation` | Die im Produkt hinterlegte Rolle enthält Rechte, die der Dienstbenutzer der Integration selbst nicht hat. Der Vertrag wird nicht umgesetzt. |
| `hoster_internal_error` | Unerwarteter Fehler, im Panel-Log nachvollziehbar |
| `product_changed_manual_resize_required` | Hinweis, kein Fehler: Tarif gewechselt, Ressourcen noch nicht angepasst |

`hoster_role_escalation` schlägt absichtlich hart fehl, statt die Rolle still auszulassen: sonst
hätte der Kunde einen Server ohne das Kontingent, für das er bezahlt hat, und niemand würde es
merken.

---

## Webhooks

MSM meldet jede Zustandsänderung an das im Panel hinterlegte HTTPS-Ziel — **wenn** dort ein
Webhook-Secret erzeugt wurde. Ohne Ziel oder Secret wird bewusst nichts eingestellt.

### Eventnamen

Ein Event heißt immer `service.<status>`. Vollständige Liste:

```
service.pending
service.provisioning
service.ready
service.suspended
service.terminating
service.terminated
service.failed
```

### Header

```
Content-Type: application/json
X-MSM-Timestamp: 1786120930
X-MSM-Signature: sha256=<hex>
X-MSM-Event: service.ready
User-Agent: MSM-Hoster-Webhook/1.0
```

### Body

Der Body ist das Response-Schema aus [Externe API](#externe-api) **ohne** `task_id`, plus das Feld
`event`:

```json
{
  "event": "service.ready",
  "external_service_id": "SVC-4711",
  "desired_state": "active",
  "status": "ready",
  "status_code": null,
  "server_id": 42,
  "correlation_id": "6f6d9d1e-6b1e-4a51-9f0c-2b7a5d3e8c14",
  "terminate_after": null,
  "updated_at": "2026-08-08T09:22:10+00:00"
}
```

Node-Namen, Hostadressen, Ports und Installationspfade gehören dem Betreiber, nicht dem Shop — sie
erscheinen absichtlich nicht.

### Zustellung

| Eigenschaft | Wert |
| --- | --- |
| Timeout je Versuch | 10 s |
| Versuche | 5 |
| Abstände | 30 s, 120 s, 600 s, 3600 s |
| `4xx`-Antwort | endgültig, **keine** Wiederholung |
| `5xx` oder Netzwerkfehler | Wiederholung nach obigem Abstand |
| Redirects | werden **nicht** verfolgt |
| Aufbewahrung der Zustellprotokolle | 30 Tage |

Zustellungen liegen in der Datenbank, nicht im Speicher: ein Panel-Neustart während eines Backoffs
verliert nichts. Endgültig fehlgeschlagene Zustellungen lassen sich im Panel manuell erneut
einplanen.

**Payload-Obergrenze: 16 KiB.** Ein größerer Body wird **still verworfen** — es entsteht keine
Zustellung und der Empfänger erfährt nichts davon. Im Normalbetrieb ist das unerreichbar (das
Beispiel oben hat 254 Byte), aber ein Empfänger sollte sich nicht darauf verlassen, jede
Zustandsänderung zu sehen. Der `GET`-Endpunkt bleibt die verbindliche Quelle.

Ein `200` ist die Bestätigung. Antwortet der Empfänger mit `4xx`, gilt die Zustellung als endgültig
abgelehnt — auch bei `401`. Wer die Signatur falsch prüft, verliert dadurch Events ohne Retry.

---

## Signatur nachrechnen

Die Signatur ist `HMAC-SHA256` über die Zeichenkette `{timestamp}.{body}`, ausgegeben als
`sha256=<hex>`. Der Zeitstempel ist Teil der signierten Daten — ohne ihn ließe sich ein
abgefangener Request beliebig oft erneut zustellen.

> **Über den Rohbody signieren, nicht über neu serialisiertes JSON.**
> MSM serialisiert mit `separators=(",", ":")` und `ensure_ascii=False`, also ohne Leerzeichen und
> mit echten Umlauten. Wer den Body erst in ein Objekt parst und dann neu serialisiert, bekommt
> nahezu sicher eine andere Zeichenkette und damit eine andere Signatur. Die meisten
> Signaturprobleme in der Praxis haben genau diese Ursache.

### Rechenbeispiel

Mit diesen Werten muss jede korrekte Implementierung denselben Digest liefern:

```
Secret:     whsec_msm_beispiel_nicht_verwenden
Timestamp:  1786120930
Body:       {"event":"service.ready","external_service_id":"SVC-4711","desired_state":"active","status":"ready","status_code":null,"server_id":42,"correlation_id":"6f6d9d1e-6b1e-4a51-9f0c-2b7a5d3e8c14","terminate_after":null,"updated_at":"2026-08-08T09:22:10+00:00"}

Erwartet:   sha256=c22272c50fb68bae6f99965c33f831b7e3197d9766c00b0fc216b6c5289a51b0
```

Prüfen lässt sich das ohne jeden Code:

```bash
printf '%s' "1786120930.{\"event\":\"service.ready\",\"external_service_id\":\"SVC-4711\",\"desired_state\":\"active\",\"status\":\"ready\",\"status_code\":null,\"server_id\":42,\"correlation_id\":\"6f6d9d1e-6b1e-4a51-9f0c-2b7a5d3e8c14\",\"terminate_after\":null,\"updated_at\":\"2026-08-08T09:22:10+00:00\"}" \
  | openssl dgst -sha256 -hmac "whsec_msm_beispiel_nicht_verwenden" -r \
  | cut -d' ' -f1
```

### PHP

```php
<?php
// Rohbody, NICHT $_POST und nicht json_decode/json_encode.
$body      = file_get_contents('php://input');
$timestamp = $_SERVER['HTTP_X_MSM_TIMESTAMP'] ?? '';
$signature = $_SERVER['HTTP_X_MSM_SIGNATURE'] ?? '';
$event     = $_SERVER['HTTP_X_MSM_EVENT'] ?? '';
$secret    = getenv('MSM_WEBHOOK_SECRET');

// Replay-Fenster: aeltere Zustellungen ablehnen.
if (!ctype_digit($timestamp) || abs(time() - (int) $timestamp) > 300) {
    http_response_code(400);
    exit;
}

$expected = 'sha256=' . hash_hmac('sha256', $timestamp . '.' . $body, $secret);
if (!hash_equals($expected, $signature)) {
    // 4xx bedeutet: MSM wiederholt NICHT. Nur bei echter Ablehnung senden.
    http_response_code(401);
    exit;
}

$payload = json_decode($body, true);
// ... Vertragszustand im Shop nachziehen ...
http_response_code(200);
```

### Python

```python
import hashlib
import hmac
import os
import time

SECRET = os.environ["MSM_WEBHOOK_SECRET"].encode()
TOLERANCE_SECONDS = 300


def verify(raw_body: bytes, timestamp: str, signature: str) -> bool:
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > TOLERANCE_SECONDS:
        return False
    expected = "sha256=" + hmac.new(
        SECRET, f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

### Regeln für den Empfänger

- **Zeitkonstant vergleichen** (`hash_equals`, `hmac.compare_digest`) — ein `==` auf Strings gibt
  über die Laufzeit Auskunft über den korrekten Präfix.
- **Replay-Fenster prüfen.** MSM setzt keines durch; empfohlen sind ±5 Minuten. Der Zeitstempel ist
  signiert, kann also nicht gefälscht werden — aber ohne Fenster ist ein alter, mitgeschnittener
  Request beliebig lange wiederverwendbar.
- **Idempotent verarbeiten.** Bei einem `5xx` oder Timeout wiederholt MSM. Der Empfänger kann
  dieselbe Zustandsmeldung mehrfach erhalten. `correlation_id` plus `status` identifiziert sie.
- **Reihenfolge nicht voraussetzen.** Bei Wiederholungen kann eine ältere Meldung nach einer
  neueren eintreffen. `updated_at` entscheidet, welche die aktuellere ist.
- **Schnell antworten.** Nach 10 s bricht MSM ab und wertet das als Fehlversuch. Die eigentliche
  Verarbeitung gehört in eine Queue.

---

## Ein-Klick-Handoff

Der Kunde klickt im Shop auf „Server verwalten" und landet angemeldet in seinem MSM-Panel — ohne
zweites Passwort und ohne separate Registrierung.

### `POST /api/hoster/v1/handoffs`

**Request-Body**

| Feld | Typ | Pflicht | Beschreibung |
| --- | --- | --- | --- |
| `external_service_id` | `string` (1–128) | ja | Vertrag, für den der Link gilt |
| `target_path` | `string` (≤128) | nein | Sprungziel im Panel. Erlaubt sind ausschließlich `/servers`, `/servers/{id}`, `/dashboard`. Default: `/servers`. Alles andere → `422`. |

```bash
curl -X POST https://panel.example/api/hoster/v1/handoffs \
  -H "X-MSM-Hoster-Key: $MSM_HOSTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"external_service_id":"SVC-4711","target_path":"/servers/42"}'
```

**Response**

```json
{
  "url": "https://panel.example/api/hoster/handoff/qJ8s...",
  "expires_at": "2026-08-08T09:27:10+00:00"
}
```

### Eigenschaften des Links

- gilt **5 Minuten** und **genau einmal**;
- ist an einen bestimmten Benutzer und Vertrag gebunden;
- führt nur auf die drei erlaubten internen Pfade — kein frei steuerbarer Redirect;
- MSM speichert ausschließlich den Hash; der Link erscheint weder im Audit noch in Logs;
- je Benutzer sind höchstens 5 Token gleichzeitig gültig, ältere verfallen;
- **jeder** Fehlerfall — unbekannt, abgelaufen, bereits verwendet — führt einheitlich auf die
  Loginseite. Aus der Antwort ist nicht ableitbar, welcher Fall vorlag.

Der Link sollte deshalb **beim Klick** erzeugt werden, nicht beim Rendern der Shop-Seite.

> **Split-Hosting beachten.** Die zurückgegebene URL wird aus `MSM_PANEL_URL` gebaut, während
> `/api/hoster/handoff/{token}` eine Backend-Route ist. Wer Frontend und API auf getrennten Hosts
> betreibt, muss sicherstellen, dass `MSM_PANEL_URL` auf einen Host zeigt, der `/api/*` an das
> Backend weiterreicht — sonst führt der Link ins Leere.

### Alternative: OIDC

Zusätzlich kann der bestehende Custom-OIDC-Login verwendet werden (Einstellungen → **OAuth**).
Beides ist gleichzeitig möglich: Handoff für den direkten Klick aus dem Shop, OIDC für einen
normalen erneuten Login im Panel.

---

## Panel-Verwaltung (Admin-Endpunkte)

Diese Endpunkte bedienen die Panel-Oberfläche. Sie nutzen **Cookie-Auth mit CSRF-Schutz**, nicht den
Hoster-API-Key. Für Automatisierung und Infrastructure-as-Code sind sie hier vollständig gelistet.

Basis: `/api/hoster`

| Methode | Pfad | Permission | Zweck |
| --- | --- | --- | --- |
| `GET` | `/integrations` | `panel.hoster.read` | Integrationen auflisten |
| `POST` | `/integrations` | `panel.hoster.write` | Integration anlegen — Antwort enthält den API-Key **einmalig** |
| `PATCH` | `/integrations/{integration_id}` | `panel.hoster.write` | Name, Status, Dienstbenutzer, Webhook-Ziel, Kulanzfrist ändern |
| `POST` | `/integrations/{integration_id}/api-key` | `panel.hoster.write` | API-Key rotieren — alter Key sofort ungültig |
| `POST` | `/integrations/{integration_id}/webhook-secret` | `panel.hoster.write` | Webhook-Secret erzeugen oder rotieren |
| `DELETE` | `/integrations/{integration_id}` | `panel.hoster.write` | Integration löschen (nur ohne aktive Verträge) |
| `GET` | `/integrations/{integration_id}/products` | `panel.hoster.read` | Produktzuordnungen lesen |
| `PUT` | `/integrations/{integration_id}/products` | `panel.hoster.write` | Produktzuordnung anlegen oder ersetzen |
| `DELETE` | `/integrations/{integration_id}/products/{product_id}` | `panel.hoster.write` | Produktzuordnung entfernen |
| `GET` | `/integrations/{integration_id}/services` | `panel.hoster.read` | Verträge dieser Integration |
| `GET` | `/integrations/{integration_id}/deliveries` | `panel.hoster.read` | Webhook-Zustellprotokoll (Statuscode, Versuch — nie Signatur oder Body) |
| `POST` | `/integrations/{integration_id}/deliveries/{delivery_id}/retry` | `panel.hoster.write` | Fehlgeschlagene Zustellung erneut einplanen |

**Produktzuordnung** (`PUT .../products`):

| Feld | Typ | Beschreibung |
| --- | --- | --- |
| `external_product_key` | `string` (1–128) | Produktkennung aus dem Shop |
| `game_type` | `string` (1–64) | Blueprint bzw. Spieltyp in MSM |
| `ram_limit_mb` | `int \| null` | 512 – 4 194 304 |
| `cpu_limit_percent` | `int \| null` | 10 – 3 200 (100 = ein Kern) |
| `disk_limit_gb` | `int \| null` | 1 – 1 048 576 |
| `node_id` | `int \| null` | Feste Node erzwingen; `null` = automatische Wahl |
| `backup_interval_hours` | `int \| null` | 1 – 8 760 |
| `role_id` | `int \| null` | Globale Rolle, die der Kunde zusätzlich zu seinen bestehenden Rollen erhält, solange ein Vertrag auf dieses Produkt aktiv ist. Leer = keine Zusatzrolle. |
| `enabled` | `bool` | Deaktivierte Produkte werden bei Bestellung abgelehnt |

Die Rolle wird bereits beim Speichern geprüft: enthält sie eine Berechtigung, die der Dienstbenutzer
der Integration selbst nicht hat, antwortet der Endpunkt mit `422`. Eine Integration kann nie mehr
vergeben, als ihr Dienstbenutzer hält — sonst wäre das Feld ein Weg, sich über einen Shop-Kauf
Rechte zu verschaffen.

Das vollständige OpenAPI-Schema ist im Panel unter `/api/docs` verfügbar (erfordert Anmeldung und
`panel.settings.read`).

---

## Betriebshinweise

- **Der Dienstbenutzer ist die Grenze.** Die Integration kann nie mehr als er. Er braucht
  `servers.create` und — für die automatische Löschung nach Ablauf der Kündigungsfrist —
  zusätzlich `servers.delete`. Der Owner-Account ist als Dienstbenutzer bewusst nicht zulässig.
- **Kunden erhalten Rechte nur auf ihrem eigenen Server**, per-Server delegiert statt über eine
  globale Rolle. Bewusst nicht enthalten: Neuinstallation, Netzwerk- und Ressourcenverwaltung,
  Container-Exec, Datenbankadministration. `servers.delete` ist global und für Kunden unerreichbar.
- **Eine Integration mit aktiven Verträgen lässt sich nicht löschen.** Ein Cascade würde die
  Zuordnung zwischen Kunden und ihren laufenden Servern zerstören, während die Server weiterlaufen.
- **Alles erscheint im Audit** (`hoster.*`) mit Origin `external` und der gemeinsamen
  `correlation_id` — ohne Secrets und ohne Kundenkennungen im Klartext.
- **MSM speichert kein Shop-Passwort und keine Zahlungsdaten.** Gespeichert werden die externe
  Kunden- und Vertragskennung sowie optional die E-Mail für die Erstanlage des Panel-Benutzers.
