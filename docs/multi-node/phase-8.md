# Phase 8: PostgreSQL-only, sichere Updates und einfache Node-Einrichtung

## Ziel

MSM soll ohne Infrastrukturwissen installierbar und aktualisierbar sein. Die
Bedienung bleibt bewusst klein: Das Panel führt, der Host erledigt die
technischen Schritte automatisch und Fehler werden nicht als Erfolg verkauft.

Phase 8 besteht aus drei zusammenhängenden Produktpfaden:

1. PostgreSQL ist die einzige unterstützte Panel-Datenbank im laufenden Betrieb.
2. Bestehende Installationen erhalten einen geprüften, einmaligen
   SQLite-nach-PostgreSQL-Import.
3. Panel-Updates und neue Nodes werden über geführte Bootstrap-Flows
   eingerichtet, ohne Tokens oder Passwörter in URLs zu transportieren.

Eine frische All-in-One-Installation benötigt nach dem SSH-Login nur:

```bash
curl -fsSL https://raw.githubusercontent.com/einmalmaik/maunting-server-manager/main/scripts/bootstrap.sh | sudo bash -s -- --domain panel.example.com
```

PostgreSQL, Redis, Rootless Docker, DIS, lokaler Agent, Caddy und systemd werden
automatisch eingerichtet. Der Owner hinterlegt beim ersten Browseraufruf nur
seinen Account, eine Absenderadresse und einen Resend-API-Key. Ein frei
wählbarer SMTP-Host ist im anonymen First-Run absichtlich nicht erlaubt, damit
die Setup-Route keinen Zugriff auf interne Netzwerkziele ermöglicht. SMTP kann
über den klassischen Installer oder nach dem Login eingerichtet werden.

## 1. Verbindliche Datenbankregeln

- Neue Installationen richten PostgreSQL automatisch auf Loopback ein.
- Das Backend startet in Produktion ausschließlich mit einer
  `postgresql+psycopg2://`-Verbindung.
- SQLite bleibt ausschließlich als read-only Quelle des einmaligen Imports
  erhalten. Es ist keine auswählbare Betriebsart mehr.
- Der Import erstellt zuerst das aktuelle PostgreSQL-Schema, kopiert nur
  bekannte Modellspalten, prüft pro Tabelle die Zeilenzahl und benennt die
  SQLite-Datei erst nach erfolgreicher Verifikation in ein Migrationsarchiv um.
- Ein nicht leeres PostgreSQL-Ziel wird niemals automatisch überschrieben.
- Schemaänderungen laufen über Alembic. `Base.metadata.create_all()` darf nur
  eine leere Datenbank initialisieren; der laufende Webprozess verändert kein
  Schema mehr nebenbei.
- Vor jedem produktiven Schema-Upgrade wird ein `pg_dump` erzeugt und geprüft.

## 2. Sicherer Updatepfad

Der Updater arbeitet in klaren Gates:

1. Zielversion und neues Updater-Skript außerhalb des Live-Verzeichnisses
   laden und syntaktisch prüfen.
2. PostgreSQL-Dump, Konfiguration und Codeversion sichern.
3. Panel in Wartung nehmen; laufende Game-Container und Remote-Agents bleiben
   unberührt.
4. Code und Abhängigkeiten aktualisieren.
5. Panel stoppen und die Datenbankmigration ausführen.
6. Lokalen Agent konfigurieren, starten und `/health` prüfen.
7. Panel starten und `/api/health` prüfen.
8. Erst danach Erfolg melden. Bei einem fehlgeschlagenen Gate bleibt der
   Fehler sichtbar und der dokumentierte Rollbackpfad wird ausgegeben.

Der Updater nimmt keine automatische destruktive Datenbank-Rückspielung vor.
Bei einem Fehler startet er ein zuvor aktives Panel nach Möglichkeit wieder,
nennt das verifizierte Dump-/SQLite-Backup und beendet sich ungleich null. Eine
bewusste Wiederherstellung bleibt eine Owner-Aktion, damit ein neuerer, bereits
erfolgreich geschriebener Datenstand nicht automatisch überschrieben wird.

Der Übergang von einem alten Updater wird einmalig mit einem außerhalb von
`/opt/msm` geladenen Phase-8-Updater gestartet. Danach übernimmt jeder Updater
vor dem Git-Wechsel selbst den geprüften Handoff an seine Zielversion.

## 3. Node-Einrichtung für Nicht-Techniker

Die sichere Minimalgrenze bleibt ein einziger Root-Befehl auf dem neuen
Linux-Host. MSM fragt weder SSH-Passwörter noch private SSH-Keys im Panel ab.

Zielablauf:

1. Owner klickt im Panel auf **Node hinzufügen**.
2. Das Panel zeigt genau einen kopierbaren Installationsbefehl. Der Befehl
   enthält nur die öffentliche Panel-URL, kein Secret.
3. Der Installer richtet Benutzer, Rootless Docker, Agent, TLS, Firewall und
   systemd ein und meldet eine kurzlebige Enrollment-Anfrage beim Panel an.
4. Im Panel erscheint die gefundene Node mit **Bestätigen**.
5. Der Agent hat seinen Token bereits lokal erzeugt und beim Enrollment-Beginn
   über HTTPS an das Panel übertragen. Das Panel hält ihn ausschließlich
   DIS-verschlüsselt; er erscheint weder in URL noch UI noch Log.
6. Nach der Bestätigung prüft das Panel Agent-Token, TLS-Pin und Erreichbarkeit.
   Die Node wird erst dann als online und auswählbar markiert.

Enrollment-Anfragen sind kurzlebig, rate-limited und bis zur Owner-Bestätigung
wirkungslos. Claim-Secrets werden nur gehasht gespeichert. Nicht bestätigte
Anfragen werden automatisch gelöscht.

## 4. Hosting und Komponenten

- Frontend und Backend dürfen getrennt laufen; Same-Origin bleibt der einfache
  Standard.
- DIS läuft privat beim Backend und wird nicht öffentlich exponiert.
- Auf jedem Workload-Host läuft genau ein Agent.
- Ein All-in-One-Host ist weiterhin ein vollständiges Node-Setup: Der lokale
  Agent wird automatisch installiert und registriert.
- Managed PostgreSQL der Game-Server bleibt node-lokal und ist nicht die
  zentrale Panel-Datenbank.

## 5. Abnahmekriterien

- Frische Installation ohne Datenbankauswahl erzeugt ein funktionsfähiges
  PostgreSQL-Panel und einen online Local Node.
- Eine synthetische Legacy-SQLite-Datenbank wird vollständig und
  wiederholbar nach PostgreSQL importiert; ein zweiter Import wird sicher
  abgelehnt.
- Ein absichtlich fehlschlagender `pg_dump`, Agent-Start, Schema-Upgrade oder
  Panel-Healthcheck kann niemals zu „Update erfolgreich“ führen.
- Ein laufender Testserver bleibt während eines Panelupdates aktiv.
- Remote-Node-Einrichtung benötigt nach dem Klick im Panel nur einen
  kopierbaren Befehl und eine Bestätigung im Panel.
- Weder Agent-Token noch Enrollment-Claim noch Datenbankpasswort erscheinen in
  URLs, Browser-Storage, Toasts, Logs oder Prozessargumenten.
- Backend-Tests, Agent-Tests, Frontend-Tests und der lokale Multi-Node-E2E-Test
  sind grün; die betroffenen UI-Routen wurden im Browser geöffnet.
