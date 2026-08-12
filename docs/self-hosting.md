# Self-Hosting, Deployment-Artefakte und Nodes

Diese Seite ist die kanonische Betriebsdokumentation für die Installation der
MSM-Komponenten. Das Repository ist ein Monorepo, die ausgelieferten Komponenten
sind trotzdem getrennte Deployment-Einheiten.

## Welche Komponente läuft wo?

Eine normale Installation verwendet einen Control-Plane-Server und beliebig
viele Nodes:

| Rolle | Enthält | Benötigt das vollständige Repository? |
| --- | --- | --- |
| Panel/Control Plane | Backend, DIS-Sidecar, Panel-PostgreSQL, Frontend und lokaler Agent | Nein, das Panel-Release enthält nur die benötigten Projektteile. |
| Separates Frontend | Fertig gebautes statisches Vite-Bundle | Nein, `msm-frontend-<VERSION>.tar.gz` genügt. |
| Remote-Node | Agent, Rootless Docker, TLS und node-eigene Serverdaten | Nein, der Node lädt sein Agent-Paket direkt vom Panel. |

Das Monorepo ist die gemeinsame Quelle für Entwicklung und Releases. Es ist
kein Zwang, jede Komponente per `git clone` auszuliefern.

## Empfohlene Panel-Installation

Eine frische Produktionsinstallation benötigt eine HTTPS-Domain, deren DNS
bereits auf den Panel-Server zeigt:

```bash
curl -fsSL https://raw.githubusercontent.com/einmalmaik/maunting-server-manager/main/scripts/bootstrap.sh \
  | sudo bash -s -- --domain panel.example.com
```

Der Bootstrap lädt bevorzugt das getestete `msm-panel-<VERSION>.tar.gz` des
neuesten GitHub-Releases. Nur wenn noch kein passendes Release existiert, nutzt
er als Kompatibilitätsfallback einen flachen Git-Checkout. Anschließend ruft er
`install.sh --simple` auf. Vor dem Entpacken wird das Archiv zwingend gegen den
mitgelieferten Eintrag in `SHA256SUMS` geprüft.

`install.sh` bleibt die zentrale Installationslogik. Der einfache Bootstrap-
Modus richtet PostgreSQL, Redis, Rootless Docker, DIS, lokalen Agent, Caddy und
systemd automatisch ein. E-Mail wird anschließend im Browser-Setup konfiguriert.
Wer bewusst den interaktiven Expertenweg benötigt, kann das Repository oder
Panel-Artefakt entpacken und `sudo bash install.sh` ausführen.

Alle drei Einstiege – direkter interaktiver Aufruf, `--simple` und öffentlicher
Bootstrap – durchlaufen dieselbe idempotente Systemvorbereitung. Sie installiert
die benötigten Basispakete auch auf einem minimalen Ubuntu-/Debian-System,
repariert eine unvollständige Caddy-Paketquelle sicher und erhält vorhandene
Caddy-Konfigurationen. Der Installer aktualisiert die von MSM benötigten Pakete,
führt aber bewusst kein pauschales Betriebssystem-`dist-upgrade` und keinen
automatischen Reboot fremder Systeme durch.

### Abgebrochene Erstinstallation fortsetzen

Hat ein abgebrochener Lauf bereits die lokale PostgreSQL-Rolle und Datenbank
`msm`, aber noch keine `backend/.env` angelegt, bleibt der normale Installer aus
Sicherheitsgründen stehen. Nach Prüfung des Zustands kann der Lauf ohne Löschen
der Datenbank ausdrücklich fortgesetzt werden:

```bash
sudo bash install.sh --simple --domain panel.example.com --resume-partial
```

Der Resume-Pfad akzeptiert ausschließlich die Datenbank `msm` mit Eigentümer
`msm`, eine unprivilegierte Rolle ohne Mitgliedschaften und ohne weitere eigene
Datenbanken. Er erzeugt ein neues Passwort, überträgt es ausschließlich über
`stdin` an PostgreSQL und schreibt es anschließend in die geschützte `.env`.
Abweichende oder fremde PostgreSQL-Zustände werden nicht verändert.

PostgreSQL ist die einzige unterstützte Panel-Runtime-Datenbank. SQLite-Code im
Installer dient ausschließlich dazu, bestehende Altinstallationen einmalig und
geprüft nach PostgreSQL zu migrieren; neue SQLite-Installationen gibt es nicht.

## Getrennte GitHub-Release-Artefakte

Der Workflow `.github/workflows/release-artifacts.yml` erzeugt für `v*`-Tags:

- `msm-panel-<VERSION>.tar.gz`: installierbare Control Plane inklusive Backend,
  DIS, Frontend, Agent-Paket und Installationsskripten.
- `msm-frontend-<VERSION>.tar.gz`: ausschließlich das gebaute `frontend/dist`
  plus öffentliche `.env.example`.
- `msm-agent-<VERSION>.tar.gz`: Agent-Quellen und Agent-Installer für
  kontrollierte Offline-/Automationsfälle.
- `SHA256SUMS`: Prüfsummen aller drei Archive.

Bei einem Tag werden die Dateien an einen zunächst als Entwurf angelegten
GitHub-Release gehängt. Ein manueller Workflow-Lauf stellt sie 30 Tage als
GitHub-Actions-Artefakt bereit.

### Separates Frontend

Für ein getrenntes Frontend wird nur das Frontend-Archiv benötigt:

```bash
VERSION=v1.8.0
curl -fLO "https://github.com/einmalmaik/maunting-server-manager/releases/download/${VERSION}/msm-frontend-${VERSION}.tar.gz"
tar -xzf "msm-frontend-${VERSION}.tar.gz"
```

Der Inhalt unter `dist/` wird von einem statischen Webserver oder Hostingdienst
ausgeliefert. `VITE_API_URL` und optional `VITE_WS_URL` sind Build-Werte und
müssen deshalb bereits beim Workflow-/Frontend-Build korrekt gesetzt sein. Ein
manueller Workflow-Lauf bietet dafür die Eingaben `vite_api_url` und
`vite_ws_url`; Tag-Releases ohne diese Werte verwenden relative URLs für ein
Frontend am gleichen Origin wie das Backend. In `VITE_*` dürfen niemals Secrets
stehen. Das Backend benötigt für getrenntes Hosting passende CORS- und
Cookie-Einstellungen; Details stehen in `frontend/.env.example` und
`backend/.env.example`.

## Bestehende All-in-one-Installation aufteilen

Für eine bereits installierte MSM-Instanz ist der interaktive Assistent der
Standardweg:

```bash
sudo /opt/msm/helper-scripts/migrate-panel-components.sh
```

Er fragt unabhängig voneinander nach drei Aktionen und führt sie anschließend
in einer sicheren Reihenfolge aus:

1. **Externes Frontend verbinden:** Das statische Frontend muss beim Anbieter
   bereits mit `VITE_API_URL=https://api.example.com` gebaut und über eine
   HTTPS-Origin erreichbar sein. Der Assistent prüft die Origin, stellt Backend,
   CORS, Cross-Site-Cookies und die API-only-Caddy-Site ein und prüft den
   Preflight. Er lädt selbst nichts zu Vercel, Wurzel oder einem anderen
   Hostinganbieter hoch, weil dafür anbieterspezifische Zugangsdaten nötig wären.
2. **Ausgewählte Gameserver verschieben:** Eine komma-separierte Liste wie
   `1,2,3,8` wird nacheinander auf einen bereits registrierten Zielnode kopiert.
   Jeder Server muss gestoppt sein. Das vollständige Serververzeichnis enthält
   Saves, Konfiguration, Mods, Workshop-Dateien, Blueprint-erzeugte Laufzeitdaten
   und Backups. Zugeordnete node-eigene PostgreSQL-Datenbanken werden als Dumps
   mitgesichert und auf dem Ziel wiederhergestellt. Die DB-Zuordnung wechselt
   erst nach erfolgreicher Zielprüfung; Quelldaten werden nicht automatisch
   gelöscht.
3. **Backend/Control Plane verschieben:** Das Ziel muss ein frischer
   Ubuntu-/Debian-Server sein, erreichbar per Root-SSH oder über einen Benutzer
   mit passwortlosem `sudo`. Der Assistent installiert zuerst parallel eine
   Backend-only-Control-Plane. Befindet sich auf der Quelle noch der lokale
   Agent, wird er über das normale, vom Owner zu bestätigende TLS-Enrollment in
   einen eigenständigen Node umgewandelt. Die bestätigte Node-ID übernimmt der
   Assistent direkt aus der geschützten Enrollment-Antwort; sie muss nicht
   abgelesen oder erneut eingegeben werden. Eine kurzlebige Challenge pro
   Serververzeichnis beweist, dass der neue Agent exakt dieselben Daten und den
   erwarteten Rootless-Docker-Runtime sieht; erst dann wird die Node-Zuordnung
   atomar geändert.

Beim finalen Backend-Cutover stoppt nur die alte Control Plane kurz. Danach
werden ein konsistenter PostgreSQL-Dump, die Backend-Konfiguration, dieselben
DIS-/Anwendungs-Secrets, Panel-Backups, Community-Blueprints und der
Setup-Zustand übertragen. Das Ziel behält sein frisch generiertes lokales
PostgreSQL-Passwort; Datenbank-URLs der Quelle werden nicht übernommen. Nach
Restore, DIS-, Backend-, Caddy- und Health-Prüfung bleibt die alte Control Plane
deaktiviert, während ihr eigenständiger Agent weiterläuft. Die Quelle wird nicht
gelöscht und kann bewusst als Rollback-Basis erhalten bleiben.

Vor dem Commit des Zielzustands startet jeder Fehler die alte Control Plane
wieder. Nach erfolgreicher lokaler Zielprüfung gibt es absichtlich keinen
automatischen Split-Brain-Fallback mehr: Es darf nie gleichzeitig auf zwei
Panel-Datenbanken geschrieben werden. Falls die öffentliche Health-Prüfung noch
nicht grün ist, bleibt das Ziel maßgeblich und der Assistent fordert zur Prüfung
von DNS und Cloud-Firewall auf.

### Voraussetzungen und unvermeidbare externe Schritte

- Die Quellinstallation läuft und verwendet PostgreSQL.
- Backend-Ziel: frisches unterstütztes Linux, mindestens die vom Preflight
  berechnete freie Kapazität, SSH-Host-Key-Prüfung und Root beziehungsweise
  passwortloses `sudo`.
- Gameserver-Ziel: bereits im Panel registrierter, online geprüfter TLS-Node mit
  genügend Speicher und freien Zielports.
- Das Frontend muss bereits beim gewählten Hostinganbieter gebaut sein. In
  `VITE_*` stehen ausschließlich öffentliche Origins, niemals Secrets.
- Den DNS-A/AAAA-Eintrag der API-Domain muss der Betreiber beim DNS-Anbieter auf
  den Zielserver setzen. Ohne Zugang zu diesem externen Anbieter darf und kann
  MSM diesen Schritt nicht vortäuschen.
- Die einmalige Owner-Freigabe eines neuen Agents bleibt eine bewusste
  Sicherheitsgrenze und wird auch mit `--yes` nicht umgangen.

Eine reine Vorprüfung ohne Änderungen ist möglich. Sie prüft die lokale
Installation und – passend zur Auswahl – Frontend-Erreichbarkeit,
Gameserver-Zustände oder die SSH-Erreichbarkeit der Ziel-Control-Plane. Sie
erstellt keine Archive, stoppt keine Dienste und verändert keine Daten:

```bash
sudo /opt/msm/helper-scripts/migrate-panel-components.sh \
  --migrate-backend \
  --backend-target root@203.0.113.10 \
  --api-domain api.example.com \
  --dry-run
```

Alle Automationsoptionen zeigt
`sudo /opt/msm/helper-scripts/migrate-panel-components.sh --help`. SSH-Passwörter,
Private Keys, Datenbankpasswörter, Agent-Tokens und DIS-Secrets werden weder als
Argumente angenommen noch ausgegeben. Temporäre Klartext-Konfigurationen und
Dumps liegen ausschließlich in Verzeichnissen mit Modus `0700`, Dateien mit
`0600`, und werden nach dem Ziel-Cutover entfernt.

## Einen neuen Node verbinden

Der Standardweg benötigt weder einen Repository-Clone noch manuelles Kopieren
von Agent-Token oder TLS-Fingerprint:

1. Als Owner im Panel **Nodes** öffnen und **Node hinzufügen** wählen.
2. Den angezeigten secret-freien Installationsbefehl kopieren.
3. Den Befehl einmal als Root auf dem neuen Ubuntu-/Debian-Node ausführen.
4. Der Installer lädt das zur Panel-Version gehörende Agent-Paket direkt vom
   Panel, installiert Rootless Docker, erzeugt Agent-Token und TLS-Zertifikat
   lokal und startet den systemd-Service.
5. Der Node sendet Token und Zertifikatsfingerprint über HTTPS an das Panel.
   Das Panel speichert den Token DIS-verschlüsselt; UI, URL und Logs zeigen ihn
   nicht an.
6. Im Panel den angezeigten kurzen Code vergleichen und die Anfrage einmalig
   bestätigen. Erst danach prüft das Panel Token, TLS-Pin und Erreichbarkeit und
   schaltet den Node frei.

Enrollment-Anfragen laufen nach 15 Minuten ab und sind rate-limited. Der
Installationsbefehl enthält kein wiederverwendbares Agent-Token. Eine bereits
registrierte Node kann über den öffentlichen Enrollment-Endpunkt niemals
überschrieben werden; eine erneute Vertrauensfreigabe bleibt eine bewusste
Owner-Aktion. Der manuelle Dialog für Host, Token und Fingerprint bleibt nur als Fallback für
bereits separat installierte oder speziell angebundene Agents bestehen. Bei
einer manuellen Agent-Installation liegt das einmal zu übernehmende Token in
`/root/msm-agent-token` mit Modus `0600`; nach dem Eintragen im Panel muss diese
Übergabedatei gelöscht werden. Im normalen Enrollment-Flow wird sie nicht
angelegt.

## Beispiel mit 20 Hosts

- Host 1: Panel/Control Plane, optional einschließlich Frontend.
- Host 2 bis 20: jeweils nur ein Remote-Agent; kein Git-Checkout.

Wenn Frontend und Backend bewusst getrennt werden, kann Host 1 das statische
Frontend, Host 2 die Control Plane und Host 3 bis 20 die Nodes betreiben. Für
größere Mengen wird derselbe secret-freie Node-Befehl über Cloud-Init, Ansible
oder eine Provider-Automation ausgeführt. Die Owner-Bestätigung bleibt eine
bewusste Sicherheitsgrenze.

## Dateien und Konfiguration

- Panel: `backend/.env.example`
- Frontend: `frontend/.env.example`
- Agent/Node: `msm-agent/.env.example`
- DIS-Sidecar: `dis-sidecar/.env.example`

Jede Vorlage erklärt Status, Zweck, Herkunft und Format aller Betreiberwerte.
Automatisch erzeugte `.env`-Dateien dürfen niemals committed werden.

### Persistenter Guardian-State des Agents

Jeder Agent hält seinen node-lokalen Guardian-Betriebszustand standardmäßig
unter `/var/lib/msm-agent/guardian`. systemd legt den Elternpfad über
`StateDirectory=msm-agent` an; er gehört dem Agent-Service und darf weder vom
Webserver ausgeliefert noch in einen Gameserver-Container gemountet werden.

- `MSM_GUARDIAN_STATE_DIR` ändert den Pfad nur für bewusst abweichende
  Installationen.
- `MSM_GUARDIAN_LOOP_INTERVAL_SECONDS` bestimmt das Reconcile-Intervall
  (Default `5.0`). Kleinere Werte erhöhen Docker-/I/O-Last und sind kein
  Ersatz für sinnvolle Probe-Intervalle.
- Enthalten sind akzeptierte Sollzustände, beobachtete Zustände, unbestätigte
  Incidents und die höchstens zehn Recovery-Lockfile-Backups pro Server.
- JSON-State wird atomar geschrieben. Beschädigte Dateien werden nicht
  überschrieben, sondern als Corruption-Incident behandelt.
- Lockfile-Backups liegen unter
  `recovery-backups/<server-id>/<backup-id>/`; einzelne Dateien sind auf 1 MiB
  begrenzt und mit Modus `0600` angelegt.

Vor einer Node-Neuinstallation oder Migration muss dieser Pfad zusammen mit den
Serverdaten gesichert werden. Für eine Wiederherstellung Agent stoppen, den
Pfad mit unverändertem Eigentümer und restriktiven Rechten zurückspielen und
erst danach den Agent starten. Eine alte State-Kopie darf nicht parallel auf
zwei Nodes aktiv sein. Nach dem Start müssen Node-Heartbeat, Guardian-Zustand
und Generation/Hash im Panel übereinstimmen.

`install.sh`, `update.sh` und `helper-scripts/install-msm-agent.sh` legen den
State-Pfad an beziehungsweise erhalten ihn. Ein Update darf ihn nicht löschen.
Bei manueller Paketierung ist `/var/lib/msm-agent` deshalb ein persistentes
Release-Artefakt, kein Cache.

Bei getrenntem Hosting bezeichnet `MSM_PANEL_URL` die vom Benutzer geöffnete
Frontend-Origin, während `MSM_API_URL` die öffentliche Backend-Origin bezeichnet.
Bei einer All-in-one-Installation sind beide identisch. Cookies werden ohne
manuellen Override aus `MSM_API_URL` abgeleitet, weil nur der API-Host sie setzen
darf. `MSM_LOCAL_AGENT_ENABLED=false` wird auf einer migrierten Backend-only-
Control-Plane automatisch gesetzt; Betreiber müssen dafür keinen Token kopieren.

## SaaS-Hosting-Betrieb: Node-Härtung, Secret-Rotation, Admin-Monitoring

Wenn du **fremde Kunden** auf denselben Nodes hostest (Vermietung / SaaS), gilt
zusätzlich zur DB-Mandantenisolation (siehe
[`multi-node/phase-7.md` §6](multi-node/phase-7.md)) dieses Betriebs-Checkliste.
Sie ergänzt Code-Gates und ersetzt kein Host-CIS-Audit.

### Node-Härtung (Host / Agent / Docker)

| Thema | Erwartung |
| --- | --- |
| SSH / Root | Wenige privilegierte Personen; kein shared root; Keys statt Passwort wo möglich |
| Agent-Port | Nur Panel→Agent über TLS (+ Fingerprint-Pinning bei Remote-Nodes); nicht ungeschützt im Internet freigeben |
| Managed Postgres | Host-Bind nur `127.0.0.1`; Game-Container über **internal** Docker-Netz (`msm-internal`) |
| Docker | Kein unnötiges `privileged`; Agent-Hardening (`cap_drop`, Container-Name-Gates) beibehalten |
| systemd | Agent-Unit mit restriktiven Defaults (`ProtectSystem` u. a. wie von Installer gesetzt) |
| Trennung | Kritische Kunden optional auf eigenen Nodes, wenn Host-Compromise inakzeptabel ist |

Produktseitig erzwingbar: Loopback-Postgres, internal net, Agent-Auth, Panel-RBAC.
Nicht erzwingbar im Code: wer SSH auf dem Host hat — das bleibt Betriebsdisziplin.

### Secret-Rotation (was und wann)

| Secret | Wo | Rotation |
| --- | --- | --- |
| Managed-Postgres-Cluster-Admin (`msm_admin`) | Panel-Setting DIS (`managed_postgres.admin_password_encrypted`) + `ALTER ROLE` auf jedem Node mit Postgres | API: `POST /api/admin/managed-postgres/rotate-admin` (Permission `system.secrets.rotate`). Nach Verdacht, Personalwechsel oder periodisch (z. B. 90 Tage) |
| Node-Agent-Token | Node-Datensatz `auth_token_enc` | Node bearbeiten / Token tauschen (`nodes.manage`); Audit: `nodes.token.update` |
| App-DB-User / Power-User | Pro Server-DB | Bestehende Rotate-Endpunkte unter `/api/servers/{id}/databases/…` |
| Panel-Owner / Admin-Passwort | Auth | Reguläre Konto-Passwort- und 2FA-Pflege |

**Wichtig (kein stilles Split-Brain):** Die Admin-Rotation wendet `ALTER ROLE`
zuerst auf den Nodes an. Schlägt ein Node **hart** fehl, nachdem andere schon
rotiert wurden:

1. MSM versucht die **vorwärts erfolgreichen** Nodes zurück auf das **alte** Passwort zu setzen.
2. Gelingt der Rollback **vollständig**: Panel behält das **alte** Secret.
3. Schlägt der Rollback **nur bei manchen** Nodes fehl: die bereits zurückgesetzten
   Nodes bekommen das **neue** Secret erneut (`re-forward`), danach speichert das
   Panel das **neue** Secret. Alle vorwärts-erfolgreichen Nodes und das Panel
   liegen wieder auf demselben Secret.
4. Nodes, die beim Vorwärts-Schritt **hart** fehlgeschlagen sind, können noch das
   alte Secret haben — das wird in der Fehlermeldung benannt (manueller Follow-up).

Antwort und Audit enthalten **nie** das Passwort.

### Admin-Monitoring (Audit)

Privilegierte Aktionen schreiben in `audit_logs` (wer / wann / action / Ziel,
Details ohne Secrets):

- `postgres.admin.rotate`, `postgres.database.*`, `postgres.user.*`, `postgres.power_user.*`, `postgres.dump`, `postgres.restore`
- `nodes.token.update`, `nodes.enrollment.approve`

**Im Panel:** Administration → **Audit** (`/admin/audit`, Permission `system.audit.read`).  
**API:** `GET /api/admin/audit-logs?limit=50&action=…`  
Ohne Recht: **403** (kein leeres OK). Nav und Route sind gesperrt.

**Cluster-Admin rotieren im Panel:** Einstellungen → Tab **Sicherheit**  
(`system.secrets.rotate`). API: `POST /api/admin/managed-postgres/rotate-admin`.
Empfehlung: regelmäßig prüfen (wöchentlich oder nach Incidents), wer Power-User
aktiviert, Dumps gezogen oder Admin-Secrets rotiert hat.

### Kurz-Checkliste vor Go-Live mit Fremdkunden

1. SSH/Root-Kreis dokumentiert und klein  
2. Remote-Nodes nur HTTPS + TLS-Fingerprint  
3. Managed-Postgres-Admin mindestens einmal prozessiert/rotiert und Prozess bekannt  
4. `system.audit.read` / `system.secrets.rotate` nur für echte Betreiber-Rollen  
5. Ersten Audit-Abruf (`GET /api/admin/audit-logs`) verifiziert  

---

## Hoster- und Shop-Anbindung (optional, Phase 6)

Ein Betreiber kann einen externen Shop an MSM anbinden. MSM übernimmt dabei
**nicht** Shop, Zahlung oder Rechnung — nur den technischen Benutzerzugang, die
Servererstellung und den Lifecycle.

**Self-Hosted bleibt unberührt.** Ohne angelegte Integration existiert kein
Verhalten dieses Abschnitts. Die Tabellen bleiben leer, es gibt keinen
zusätzlichen Dienst und keinen offenen Endpunkt, der ohne API-Key etwas tut.

> Dieser Abschnitt beschreibt **Einrichtung und Betrieb**. Wer den Shop
> tatsächlich anbindet, braucht die vollständige Referenz mit allen
> Request- und Response-Feldern, Webhook-Nutzlasten, Eventnamen und einem
> nachrechenbaren HMAC-Beispiel: **[hoster-api.md](hoster-api.md)**, im Panel
> auch unter *Hilfe → Hoster-API*.

### Einrichtung im Panel

Einstellungen → Tab **Hoster** (Permission `panel.hoster.read`, Änderungen
`panel.hoster.write`).

1. **Dienstbenutzer anlegen.** Ein normaler Panel-Benutzer mit einer Rolle, die
   `servers.create` (und für automatische Löschung nach Kündigung zusätzlich
   `servers.delete`) enthält. Der Owner-Account ist bewusst nicht zulässig.
   Die Integration kann nie mehr als dieser Benutzer — das ist die
   Sicherheitsgrenze, nicht der API-Key.
2. **Integration anlegen.** Name, Slug, Dienstbenutzer-ID, optional das
   HTTPS-Webhook-Ziel und die Aufbewahrungsfrist nach Kündigung (Default 7 Tage).
   Der API-Key wird **genau einmal** angezeigt; MSM speichert nur seinen
   SHA-256-Hash.
3. **Produkte zuordnen.** Jede Shop-Produktkennung wird auf einen Blueprint und
   ein Ressourcenpaket abgebildet (RAM, CPU, Speicher, optional feste Node und
   Backup-Intervall). Der Shop muss keine internen MSM-IDs kennen.
4. **Webhook-Secret erzeugen**, falls der Shop Statusmeldungen empfangen soll.
   Auch dieser Wert erscheint nur einmal.

### Externe API

Authentifizierung über den Header `X-MSM-Hoster-Key`. Es gibt keinen
Cookie-Pfad; eine Browser-Session kann diese Endpunkte nicht ansprechen.

| Endpunkt | Zweck |
| --- | --- |
| `GET /api/hoster/v1/health` | API-Key prüfen, ohne etwas zu ändern |
| `PUT /api/hoster/v1/services/{external_service_id}` | Gewünschten Zustand setzen (`active`, `suspended`, `terminated`) |
| `GET /api/hoster/v1/services/{external_service_id}` | Tatsächlichen Zustand abfragen |
| `POST /api/hoster/v1/handoffs` | Einmal-Link in das Panel des Kunden erzeugen |

Beispiel für eine Bestellung:

```bash
curl -X PUT https://panel.example/api/hoster/v1/services/SVC-4711 \
  -H "X-MSM-Hoster-Key: $MSM_HOSTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"desired_state":"active","external_subject":"CUST-1234","product_key":"mc-8gb"}'
```

Derselbe Aufruf darf beliebig oft wiederholt werden: `(Integration,
external_service_id)` ist eindeutig, ein Netzwerk-Retry erzeugt keinen zweiten
Server. Schlägt die Provisionierung fehl, bleibt der Vertrag mit
`status: "failed"` und einem stabilen Fehlercode abfragbar.

### Lebenszyklus

- `active` → Server wird bei Bedarf erstellt, Kundenrechte werden gesetzt.
- `suspended` → Server wird gestoppt, Kundenrechte werden entzogen. Der
  **Panelaccount des Kunden bleibt bestehen** und eine spätere Entsperrung ist
  möglich.
- `terminated` → Server wird gestoppt und eine Frist gesetzt. **Es wird nichts
  sofort gelöscht.** Erst ein Wartungslauf (jede Minute) entfernt den Server
  nach Ablauf von `terminate_grace_days` über denselben Löschpfad wie das Panel.

### Webhooks

MSM signiert jede Zustellung mit HMAC-SHA256 über `timestamp.body`:

```
X-MSM-Timestamp: 1786120930
X-MSM-Signature: sha256=<hex>
X-MSM-Event: service.ready
```

Der Empfänger berechnet dieselbe Signatur mit dem Webhook-Secret und vergleicht
zeitkonstant. Das Secret selbst wird **nie** übertragen. Zustellungen sind
persistiert: fünf Versuche mit wachsendem Abstand, 4xx ohne Wiederholung, und
ein Panel-Neustart während eines Backoffs verliert nichts. Fehlgeschlagene
Zustellungen lassen sich im Panel manuell erneut einplanen.

### Ein-Klick-Handoff

`POST /api/hoster/v1/handoffs` liefert eine URL, die fünf Minuten und genau
einmal gilt und nur auf `/servers`, `/servers/{id}` oder `/dashboard` führen
kann. MSM speichert ausschließlich den Hash; der Link erscheint weder im Audit
noch in Logs. Jeder Fehlerfall — unbekannt, abgelaufen, bereits verwendet —
führt einheitlich auf die Loginseite.

Alternativ oder zusätzlich kann der bestehende Custom-OIDC-Login des Hosters
verwendet werden (Einstellungen → **OAuth**).

### Betriebshinweise

- Der API-Key ist gleichwertig zu den Rechten des Dienstbenutzers. Bei Verdacht
  im Panel rotieren — der alte Key ist sofort ungültig.
- Eine Integration mit noch aktiven Verträgen lässt sich nicht löschen. Das ist
  Absicht: ein Cascade würde die Zuordnung zwischen Kunden und ihren laufenden
  Servern zerstören, während die Server weiterlaufen.
- Alle Vorgänge erscheinen im Audit (`hoster.*`) mit Origin `external` und einer
  gemeinsamen Korrelations-ID, ohne Secrets und ohne Kundenkennungen im Klartext.

---

## Zugangsdaten: panelweit, pro Benutzer, pro Server (Phase 7)

GitHub-Token und Steam-Konto können auf drei Ebenen existieren. **Für einen
Self-Hosted-Betrieb ändert sich nichts:** ohne eigene Zuordnung gilt weiterhin
der panelweite Zugang.

### Auflösungsreihenfolge

1. **Server-Zuordnung** — das diesem Server ausdrücklich zugewiesene
   Benutzer-Credential.
2. **Umgebungsvariable** — `MSM_GITHUB_CLONE_TOKEN` usw., wie bisher.
3. **Panel-Zugang** — Einstellungen → GitHub bzw. Steam Account.

Die Zuordnung steht oben, weil sie die spezifischste Aussage ist. Stünde ENV
davor, wäre eine bewusst gesetzte Kundenzuordnung wirkungslos.

### Bedienung

| Wo | Was |
| --- | --- |
| Profil → **Zugangsdaten** | Jeder Benutzer hinterlegt eigene Steam-Konten oder GitHub-Token. Der Wert wird verschlüsselt gespeichert und **nie wieder angezeigt** — sichtbar bleiben nur Bezeichnung, Benutzername und die letzten vier Zeichen. |
| Server-Detail → **Zugangsdaten** | Erscheint nur, wenn der Blueprint dieses Servers welche verlangt. Zeigt die Herkunft und erlaubt die Zuordnung. Erfordert `server.credentials.manage`. |
| Einstellungen → **Hoster** | Schalter *Zentraler Fallback*: darf ein Server ohne eigene Zuordnung den panelweiten Zugang mitbenutzen? |

### Für Hoster

Den zentralen Fallback **abschalten**. Dann läuft kein Kundenserver mehr
unbemerkt mit den Zugangsdaten des Betreibers — ein Server ohne eigene
Zuordnung meldet stattdessen einen verständlichen Fehler. Kunden erhalten auf
ihrem eigenen Server automatisch `server.credentials.manage` und können ihr
Steam-Konto selbst hinterlegen.

Ein Benutzer kann ausschließlich **eigene** Zugangsdaten zuweisen. Serverrechte
allein erlauben es nicht, fremde Zugangsdaten in Betrieb zu nehmen.

### Grenzen (bewusst)

- Der **Steam-Web-API-Key bleibt panelweit**. Er dient Workshop-Metadatenabfragen
  und nicht dem Zugriff auf Daten eines einzelnen Kunden; der Steam-Client ist
  zudem ein prozessglobaler Singleton.
- Ein gebundenes Credential kann nicht gelöscht werden, solange ein Server es
  verwendet — sonst fiele dieser Server bei der nächsten Installation unbemerkt
  auf den Panel-Zugang zurück.
- Ein nicht mehr entschlüsselbares Credential (typisch nach `MSM_SECRET_KEY`-
  Rotation) führt zu einem klaren Fehler, **nicht** zu einem stillen Rückfall.

---

## KI-Werkzeuge und autonomer Modus (Phase 8)

**Ohne konfigurierten Provider passiert nichts von alledem.** Ein frisch
installiertes Panel hat keinen KI-Anbieter und alle Rollenkontingente stehen auf
0 — die KI ist damit aus, bis ein Betreiber sie unter *Einstellungen → KI*
einrichtet.

### Was die KI tun kann

Die KI verwendet ausschließlich vorhandene MSM-Funktionen; es gibt keine freie
Befehlsausführung und keinen eigenen Downloadpfad.

**Lesend** (jedes Werkzeug prüft sein eigenes Recht): Serverstatus, Node-Kapazität,
Logausschnitt, Konfigurationsdatei, Ports, Mods, Backups, Guardian-Vorfälle,
bisherige KI-Aktionen, ausstehende Modupdates, Workshop-Suche. Im Panel-Chat
zusätzlich die Blueprint-Liste und die Hostkapazität.

**Schreibend** — jedes erzeugt zunächst nur einen sichtbaren Vorschlag:
Start/Stop/Neustart, Backup, revisionsgebundene Konfigurationsänderung,
Mod-Installation, Servererstellung, das Löschen einer einzelnen Datei und die
Reparatur der Anlage.

Die Reparatur (`propose_server_repair`) nimmt **eine Kennung aus einer festen
Liste** entgegen — `repair_permissions` oder `reallocate_port` — und niemals
einen Pfad, ein Kommando oder einen Containernamen. Der Containername entsteht
in jedem Zweig aus `container_name_for(server_id)`. Das ist die mechanische
Seite der Zusage, dass es keinen Weg von einer Modellausgabe zu einer
Befehlszeile gibt.

Die Servererstellung läuft über denselben `server_provisioning_service` wie ein
Klick im Panel und eine Shop-Bestellung. Blueprintprüfung, Kapazität, Portvergabe,
Installation und Rollback sind identisch — es gibt bewusst keinen zweiten Weg,
einen Server anzulegen.

### Autonomer Modus

Standard ist der unterstützte Modus: die KI analysiert, schlägt vor, wartet.

Autonomie verlangt **vier** Bedingungen gleichzeitig:

1. die Berechtigung `ai.autonomous.use`;
2. eine ausdrückliche Freigabe des Benutzers — pro Server im KI-Tab des Servers
   oder panelweit unter *KI*. Eine Freigabe für einen konkreten Server gewinnt
   über die panelweite, **auch wenn sie abschaltet**;
3. ein Werkzeug, das nicht auf der Immer-bestätigen-Liste steht (Löschen, Wipe,
   Neuinstallation, Backup-Wiederherstellung, Blueprint-Wechsel, Secret-Rotation,
   Rechteänderung);
4. freies Stundenbudget (Standard 10 Aktionen). Ist es erschöpft, **schlägt
   nichts fehl** — die KI fragt einfach wieder nach.

> **Was Autonomie nicht entfernt.** Sie ersetzt genau einen Schritt: die
> Bestätigung durch einen Menschen. Die Rechteprüfung läuft weiterhin dreimal
> (Vorschlag, Freigabe, unmittelbar vor der Ausführung), der Server-Mutex gilt,
> und jede Aktion steht im Audit mit `autonomous: true`. Die KI kann nichts, was
> der handelnde Benutzer nicht selbst dürfte.

Ein Betreiber, der Autonomie grundsätzlich erlauben, aber auf einem empfindlichen
Server ausschließen will, legt dort eine Freigabe mit `enabled: false` an.

### Guardian weckt die KI

Mit erteilter Freigabe hat der autonome Modus eine zweite Wirkung: ein
Guardian-Vorfall startet einen Heilungslauf, **ohne dass jemand am Panel sitzt**.
Ein Scheduler-Job sieht alle sechzig Sekunden nach offenen Vorfällen.

- **Der Akteur ist der Freigeber**, nicht ein Dienstbenutzer und nicht der
  Serverbesitzer: derjenige Benutzer, der für diesen Server die Autonomie
  eingeschaltet hat und zusätzlich `ai.chat.use`, `ai.autonomous.use` und
  `server.view` besitzt. Verbrauch und Verantwortung liegen bei ihm. Gibt es
  mehrere, gewinnt die serverbezogene Freigabe vor der panelweiten, danach die
  kleinste Benutzer-ID.
- **Ohne Freigabe passiert nichts** — kein Lauf, kein Anbieteraufruf, keine
  Tokens. Der Vorfall wird nur vorgemerkt und beim nächsten Chat des Benutzers
  als Meldung des Panels erwähnt.
- **Der Lauf läuft im normalen Chat.** Er startet nur, wenn dort gerade nichts
  läuft. Schreibt der Mensch dazwischen, gewinnt er; der Verlauf der Heilung
  bleibt stehen, und ein „mach weiter" genügt.
- **Die Werkzeugmenge ist enger** als im Chat (`GUARDIAN_HEILUNG_TOOLS`):
  Lesewerkzeuge, Backup, Lifecycle, Konfiguration, Dateilöschung, Reparatur,
  Doku. Nicht dabei sind Gedächtnis, Skills, Hoster, Blueprint-Wechsel,
  Servererstellung und -löschung sowie die Websuche. Die `server_id` steht fest
  im Lauf; ein Werkzeugaufruf auf einen anderen Server wird abgewiesen, bevor er
  aufgelöst wird.
- **Vor jedem schreibenden Eingriff muss ein Backup nachgewiesen sein.** Nicht
  „angestoßen", sondern nachgewiesen: `backups.verified_at` ist gesetzt, das
  Archiv ist nicht leer, seine sha256 wurde gerechnet, und es ist jünger als der
  Vorfall. Fehlt der Nachweis, endet die Aktion mit `AI_BACKUP_UNVERIFIED` und
  die ganze Runde bricht ab — sonst folgte auf ein gescheitertes Backup ein
  Löschvorgang. Das Archiv heißt `Guardian-Heilung – <typ> – <uuid>`, damit im
  Backup-Reiter erkennbar ist, wozu es gehört.
- **Guardians eigene Heilungsleiter pausiert** währenddessen über
  `guardian_recovery_suspension_lease`. Die **Quarantäne hebt die KI nie auf** —
  die gehört dem Agenten.
- **Im Audit steht `origin=system`** statt `origin=ai`. Damit ist unterscheidbar,
  ob ein Mensch die KI gebeten hat oder ein Vorfall sie geweckt.
- **Danach eine E-Mail an den Freigeber**, bei jedem Ausgang — auch bei
  „nicht behoben". Der Text stammt vom Modell und ist HTML-escaped; „behoben"
  steht nur dort, wenn der Lauf sauber endete **und** Guardian den Vorfall als
  gelöst sieht.

Der Schalter erklärt beides in einem Dialog, in beide Richtungen: Ausschalten
heißt, dass Vorfälle danach nur noch gemeldet und nicht mehr behoben werden.

### Was an den Anbieter geht

Nachricht, begrenzte Historie, freigegebene Servermetadaten und die Ergebnisse
der Werkzeugaufrufe — jeweils durch `redact_sensitive_text` geführt und
längenbegrenzt. Die Schwärzung sitzt an **einer** Stelle: dort, wo ein
Werkzeugergebnis für das Modell serialisiert wird. Vorher schwärzte jeder
Handler für sich, und mehrere taten es gar nicht.

Erkannt und ersetzt werden Secrets, Token, Schlüssel und E-Mail-Adressen. Bei
Freitext aus einem Server (Logzeilen, Dateiinhalte, Vorfallbeschreibungen) kommen
**öffentliche** IP-Adressen dazu; private, Loopback- und Link-Local-Adressen
bleiben stehen, weil eine geschwärzte Bind-Adresse jede Netzwerkdiagnose
unmöglich machte. **Spielernamen sind nicht mustererkennbar** und können in
Logausschnitten stehen — das ist die Grenze dieser Zusage, und sie steht so auch
in der Datenschutzerklärung.

Tool-Ergebnisse werden für Rückfragen im selben Chat gespeichert
und mit dem Chat gelöscht. Alles, was aus einem Server stammt (Logs, Configs,
Memory, Anhänge), ist im Modellkontext ausdrücklich als `untrusted` markiert.

Der Prompt ist dabei **nicht** die Sicherheitsgrenze. Selbst wenn ein Modell
einer in ein Gameserver-Log geschriebenen Anweisung folgt, scheitert die
Umsetzung an RBAC, der Tool-Allowlist, den Pfadgrenzen und der
Bestätigungspflicht.

---

## Kubernetes

Manifeste und Betriebsablauf liegen unter
[`deploy/kubernetes/`](../deploy/kubernetes/README.md).

**Wichtig zur Abgrenzung:** Kubernetes betreibt dort die **Control Plane**
(Panel, DIS-Sidecar, optional PostgreSQL). Gameserver laufen unverändert als
Docker-Container über den `msm-agent` auf angebundenen Nodes — sie werden
**nicht** zu Pods. Kapazität wächst weiterhin über weitere MSM-Nodes.

Zwei Punkte, die man nicht ändern sollte, ohne die Folgen zu kennen:

- Der **DIS-Sidecar läuft im selben Pod** wie das Panel. Er bindet nur an
  `127.0.0.1`; ein eigener Service würde die Krypto-Schnittstelle clusterweit
  erreichbar machen.
- **Genau eine Panel-Replica**, Rollout-Strategie `Recreate`. Scheduler-Jobs,
  Lifecycle-Sperren und der Settings-Cache liegen im Prozessspeicher — zwei
  gleichzeitige Panels würden Auto-Restarts, Backups und Webhook-Zustellungen
  doppelt ausführen.

---

## Aktualität dieser Dokumentation

Änderungen an Bootstrap, `install.sh`, `update.sh`, Node-Enrollment,
`helper-scripts/migrate-panel-components.sh`, Release-Artefakten, Komponentenaufteilung oder
Environment-Verträgen müssen in demselben Commit sowohl diese Datei als auch die
sichtbare Panel-Seite `/docs/self-hosting` aktualisieren.
