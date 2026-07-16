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
manuelle Dialog für Host, Token und Fingerprint bleibt nur als Fallback für
bereits separat installierte oder speziell angebundene Agents bestehen.

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

## Aktualität dieser Dokumentation

Änderungen an Bootstrap, `install.sh`, `update.sh`, Node-Enrollment,
Release-Artefakten, Komponentenaufteilung oder Environment-Verträgen müssen in
demselben Commit sowohl diese Datei als auch die sichtbare Panel-Seite
`/docs/self-hosting` aktualisieren.
