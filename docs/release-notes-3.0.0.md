# Release Notes — Maunting Server Manager v3.0.0 (Major Release)

---

## 🛡️ Guardian Autonomous Engine

Der Maunting Server Manager v3.0.0 führt die **Guardian Autonomous Engine** ein — ein autonomes, ausfallsicheres Monitoring- und Self-Healing-System für Spiele- und Anwendungsserver. Die Engine überwacht Server in Echtzeit auf Agent- und Backend-Ebene, erkennt Abstürze oder fehlerhafte Zustände selbstständig und führt automatisch Reparaturmaßnahmen durch.

### Kernfunktionalitäten der Guardian Engine

#### 1. Autonomes Self-Healing & Recovery Ladder
- **Echtzeit-Probes**: Überwachung über die eingebauten, vertraglich geprüften Typen `Process`, `TCP`, `UDP-Mapping`, `HTTP`, `Minecraft Status/Query` und `Source Query`.
- **Begrenzte Recovery Ladder**: Pro Fehlertyp konfigurierbare Stufen aus Container-Restart, sanftem Restart, sicher deklarierter Lockfile-Bereinigung und Quarantäne. Nicht implementierte Host-Reboots, Node-Migrationen oder Update-Rollbacks werden nicht angeboten.
- **Backup vor Lockfile-Bereinigung**: Deklarierte Lockdateien werden vor einer riskanten Löschaktion begrenzt und mit restriktiven Rechten im persistenten Agent-State gesichert.
- **Quarantäne-Modus**: Verhindert endlose Neustart-Schleifen (Crash Loops) und Ressourcen-Fresser. Wenn ein Server trotz mehrerer Reparaturversuche nicht stabil läuft, versetzt ihn die Engine automatisch in den Quarantäne-Zustand und benachrichtigt die Administratoren.
- **1-Click Vorfalls-Auflösung**: Im Dashboard (neuer Tab **Guardian**) sehen Betreiber alle aktiven und vergangenen Vorfälle inkl. Ausführungs-Logs. Ein Klick auf *Quarantäne aufheben / Vorfall beheben* setzt den Server-Status zurück und hebt die Quarantäne auf.

#### 2. Intelligente Recovery Leases (Aussetzung bei Admin-Aktionen)
- Die Guardian Engine unterscheidet strikt zwischen einem unerwarteten Server-Absturz und beabsichtigten Aktionen des Administrators.
- Bei manuellen Starts, Stopps und Restarts werden **Recovery Leases** innerhalb eines harten Vier-Stunden-Limits automatisch gesetzt, sodass Guardian nicht in laufende administrative Eingriffe eingreift.

#### 3. Vorfalls-Benachrichtigungen (Discord Webhooks & E-Mail Alerts)
- **E-Mail-Benachrichtigungen**: Automatische Benachrichtigung an alle berechtigten Serverbetreiber bei kritischen Ereignissen oder Quarantäne.
- **Discord Webhook Embeds**: Vollständige Unterstützung von Discord-Webhooks. Wenn eine Discord-Webhook-URL im Server-Dashboard hinterlegt ist, formatiert das System den Event-Payload automatisch in visuell aufbereitete **Discord Embeds** mit Farbcodes für den jeweiligen Serverstatus.

---

## 🗂️ Neuer Datei-Arbeitsbereich

Der bisherige Dateimanager wurde in v3.0.0 zu einem vollständigen Arbeitsbereich für die tägliche Serververwaltung ausgebaut. Dateien lassen sich übersichtlich durchsuchen, parallel bearbeiten und sicher auf frühere Stände zurücksetzen — auf großen Bildschirmen ebenso wie auf Smartphone und Tablet.

### Übersichtliche Navigation
- **Hierarchischer Dateibaum**: Ordner und Unterordner werden in ihrer tatsächlichen Struktur dargestellt und können direkt auf- und zugeklappt werden.
- **Schnelle Dateisuche**: Dateien und Ordner lassen sich innerhalb des freigegebenen Server-Verzeichnisses über eine zentrale Suche finden.
- **Verzeichnisübersicht**: Der Arbeitsbereich zeigt auf einen Blick, wie viele Dateien und Ordner enthalten sind und wie viel Speicherplatz die Dateien belegen.
- **Dateiinformationen in Echtzeit**: Größe, Änderungszeit, Besitzer, Gruppe und Berechtigungen der geöffneten Datei bleiben automatisch aktuell.

### Leistungsfähiger Editor
- **Mehrere Dateien gleichzeitig**: Geöffnete Dateien bleiben in Tabs verfügbar und können ohne ständiges Neuöffnen gewechselt werden.
- **CodeMirror-Editor**: Syntaxdarstellung für gängige Konfigurations- und Programmiersprachen, darunter INI, JSON, YAML, XML, Markdown, JavaScript, TypeScript, Python, Shell, SQL, CSS, C/C++, Java, C#, Go, Rust, Lua, TOML und Dockerfiles.
- **Originalformat bleibt erhalten**: Zeilenenden und einzeilige Konfigurationen — beispielsweise Palworld-Konfigurationen — werden beim Bearbeiten nicht automatisch umformatiert.
- **Suchen und Ersetzen**: Eigene Bedienelemente für einzelne Treffer, alle Treffer, Ersetzen und „Alle ersetzen“ sind direkt im Editor verfügbar.
- **Gewohnte Tastenkürzel**: Speichern mit `Strg + S` beziehungsweise `Cmd + S` und Öffnen der Suche mit `Strg + F` beziehungsweise `Cmd + F`.

### Sicheres Speichern und Versionsverlauf
- **Autosave**: Änderungen werden nach kurzer Pause automatisch gespeichert. Autosave kann jederzeit deaktiviert und durch manuelles Speichern ersetzt werden.
- **Schutz vor Bearbeitungskonflikten**: Wurde eine Datei zwischenzeitlich an anderer Stelle verändert, überschreibt der Editor diese Änderung nicht stillschweigend. Der lokale Entwurf bleibt erhalten und kann geprüft werden.
- **Versionsverlauf**: Für bearbeitbare Textdateien bis 512 KiB können frühere Dateistände direkt im Dateimanager ausgewählt und wiederhergestellt werden. Vor einer Wiederherstellung wird auch der aktuelle Stand gesichert.
- **Speicherschonende Aufbewahrung**: Unveränderte Stände werden nicht doppelt gespeichert; der Verlauf ist bewusst auf die drei neuesten Versionen pro Datei begrenzt.
- **Kein versehentliches Überschreiben**: Beim Anlegen einer neuen Datei wird eine bereits vorhandene Datei mit demselben Namen nicht geleert oder ersetzt.

### Dateiaktionen ohne überladene Werkzeugleiste
- **Intelligente Aktionsmenüs**: Neue Datei, neuer Ordner und Upload sind kompakt gruppiert. Aktionen für ausgewählte Dateien erscheinen nur dann, wenn sie benötigt werden.
- **Kontextmenü und Drag-and-drop**: Dateien und Ordner können per Rechtsklick umbenannt, verschoben, heruntergeladen oder gelöscht und innerhalb des Dateibaums per Drag-and-drop verschoben werden.
- **Upload mit Fortschritt**: Dateien können über die Dateiauswahl oder per Drag-and-drop hochgeladen werden; größere Uploads werden fortsetzbar übertragen.
- **Responsive Bedienung**: Dateibaum und Detailansicht werden auf kleinen Bildschirmen als übersichtliche, touchfreundliche Bereiche eingeblendet.

### Klare Sicherheitsgrenze
Der Datei-Arbeitsbereich zeigt ausschließlich das für den ausgewählten Gameserver freigegebene Server-Verzeichnis. Er gewährt keinen allgemeinen Root- oder Host-Dateisystemzugriff. Sichtbare Aktionen richten sich nach den zugewiesenen Benutzerrechten; die endgültige Berechtigungsprüfung erfolgt weiterhin auf dem Server.

SQL-Dateien können als Text bearbeitet werden. Binäre SQLite-Datenbanken wie `game.db` werden in v3.0.0 bewusst nicht als Tabelleneditor geöffnet und können bei Bedarf weiterhin heruntergeladen werden.

### Verbesserte Ressourcenübersicht
- **CPU, RAM, Speicher und Uptime**: Die wichtigsten Serverwerte wurden visuell vereinheitlicht und sind schneller erfassbar.
- **Klare Grenzwerte**: Auslastung und verfügbare Limits werden kompakt dargestellt, ohne die Serveransicht mit zusätzlichen Diagrammen zu überladen.

---

## 🚀 Allgemeine Verbesserungen & Bugfixes

Außerhalb der Guardian Engine enthält v3.0.0 folgende Optimierungen und Fehlerbehebungen:

### Installation & Updates (`update.sh`)
- **Dynamische Branch-Erkennung**: Das Update-Skript erkennt automatisch den aktiven Entwicklungs-Branch (`dev/feature`) und führt Updates auf Wunsch direkt vom aktuellen Branch aus, anstatt hart auf `main` gebunden zu sein.
- **Force-Update Flag (`--force`)**: Das Skript unterstützt nun den Parameter `--force`, um Aktualisierungen auch dann erneut durchzuführen, wenn die Versionsnummern identisch sind.

### Console & Live-Stream
- **Monotoner Puffer & Reconnect-Stabilität**: Live-Konsolen-Logs nutzen nun synchrone Monotonic Line IDs und Puffer-Garantien. Trennungen der WebSocket-Verbindung führen nicht mehr zum Verlust von Konsolenzeilen; Offline- und Hintergrund-Events werden nahtlos nachgeladen.

### Benutzer- & Serverberechtigungen
- **Neue Berechtigungsübersicht**: Benutzerkonten, Status, E-Mail-Verifizierung und zugewiesene Rollen sind in einer ruhigen, übersichtlichen Oberfläche auf einen Blick erfassbar. Das eigene Konto wird eindeutig gekennzeichnet.
- **Schnellere Serverauswahl**: Serverberechtigungen werden über eine durchsuchbare Serverliste verwaltet. Auch sehr lange Servernamen bleiben vollständig lesbar und werden nicht mehr im Auswahlfeld abgeschnitten.
- **Klarer Serverkontext**: Der aktuell ausgewählte Server wird während der Berechtigungsverwaltung dauerhaft und gut sichtbar angezeigt. Dadurch lassen sich Zugriffsrechte sicherer dem richtigen Server zuordnen.
- **Kompakte Zugriffsverwaltung**: Bereits berechtigte Benutzer und der Umfang ihrer Serverrechte werden übersichtlich zusammengefasst. Die bekannte Detailansicht zum Bearbeiten bleibt direkt erreichbar.
- **Für alle Bildschirmgrößen optimiert**: Benutzer- und Berechtigungsverwaltung passen sich an Desktop, Tablet und Smartphone an, ohne wichtige Informationen oder Aktionen abzuschneiden.

### Systemd & Sicherheit
- **Agent-State Berechtigungen**: Anpassung der Systemd-Unit für den MSM-Agenten, sodass Schreibzugriffe auf `/var/lib/msm-agent` auch unter `ProtectSystem=strict` sicher gewährleistet sind.
- **Lokalisierung (i18n)**: Neue Fehlerübersetzungen für Node-Client-Verbindungsfehler und vereinheitlichte Fehlermeldungen im gesamten Frontend.

### Ressourcen & Guardian (Hotfix)
- **CPU/RAM live speichern**: Laufende Server können wieder zuverlässig über die Ressourcen-Einstellungen angepasst werden. Zuvor schlug das Speichern mit einem generischen Fehler fehl, obwohl der Server erreichbar war — die neuen Grenzen greifen jetzt ohne Neustart.
- **Guardian-Sync stabilisiert**: Nach dem Aufheben einer Quarantäne kann die Überwachung den gewünschten Zustand wieder sauber übernehmen. Endlose Sync-Konflikte („Konfigurationsprüfsumme weicht ab“) nach erfolgreicher Freigabe gehören damit der Vergangenheit an.
- **Gilt auf jedem Node**: Der Fix sitzt im Agenten und im Panel-Contract — lokale und entfernte Worker verhalten sich gleich.

### Managed PostgreSQL: Power-User klar und mandantensicher
Für Server mit integrierter PostgreSQL-Datenbank wurde die Darstellung und Benennung des **Power-User**-Zugangs überarbeitet — vor allem für Betreiber, die mehrere Kunden oder Server auf denselben Nodes hosten.

- **Keine irreführende „Superuser“-Bezeichnung mehr**: In der Datenbank-Oberfläche und in den Texten heißt es jetzt konsequent **Power-User**. Der frühere Eindruck, man erhalte einen clusterweiten PostgreSQL-SUPERUSER mit Zugriff außerhalb der eigenen Datenbank, war falsch und ist behoben.
- **Gilt nur für diese eine Datenbank**: Power-User aktiviert erweiterte Owner-Credentials **ausschließlich für die ausgewählte Server-Datenbank**. Ein aktivierter Power-User bei Kunde A hat **keinen** Zugriff auf die Datenbank von Kunde B.
- **Keine systemweiten Rechte**: Auch im Power-User-Modus bleibt die Rolle ohne Cluster-SUPERUSER und ohne Rechte, neue Datenbanken oder Rollen clusterweit anzulegen. Isolation zwischen Servern/Kunden bleibt erhalten.
- **Klarere Hinweise beim Aktivieren, Rotieren und Entziehen**: Dialoge und Bestätigungstexte erklären den Geltungsbereich (nur diese DB, Passwort nur einmal sichtbar, nicht in Tickets/Logs teilen).
- **Technisch unverändert stark**: Getrennte Datenbanken und Rollen pro Server, Netzwerkbindung und Panel-Berechtigungen bleiben die Grundlage der Isolation; die Texte und API-Felder (`is_power_user`) beschreiben das jetzt korrekt.

### SaaS-Betrieb: Secret-Rotation und Admin-Audit
Für Betreiber, die **fremde Kunden** auf denselben Nodes hosten, gibt es jetzt konkrete Betriebsfunktionen und Dokumentation:

- **Cluster-Admin-Passwort rotieren**: Das interne Managed-Postgres-Admin-Secret (`msm_admin`) kann im Panel unter **Einstellungen → Sicherheit** (oder per Admin-API) sicher erneuert werden. Es wird auf den Nodes und im Panel aktualisiert — das neue Passwort erscheint **nicht** in der UI, Antwort oder Audit-Log.
- **Admin-Audit-Log**: Wichtige privilegierte Aktionen (DB anlegen/löschen, Power-User, Dump/Restore, Node-Token, Enrollment-Freigabe, Admin-Rotation) werden dauerhaft protokolliert (wer / wann / was), ohne Passwörter oder Tokens zu speichern.
- **Im Panel sichtbar**: Unter **Administration → Audit** (`/admin/audit`) für Nutzer mit `system.audit.read`. Andere Benutzer sehen den Menüpunkt nicht und erhalten auf der Route **403**.
- **Betriebs-Checkliste**: In der Self-Hosting-Doku stehen Node-Härtung, was wann zu rotieren ist und wie man Admin-Aktivität prüft.

### Sicherheit: Konfigurierbare Login- und API-Rate-Limits
Unter **Einstellungen → Sicherheit** lassen sich zwei zentrale Anfrage-Limits pro IP anpassen — ohne Backend-Neustart und ohne Code-Änderung. Das schützt Login und Panel-API vor Brute-Force und Überlastung und bleibt für Firmen-IPs oder eigene Automationen flexibel.

- **Login- und Authentifizierungs-Limit**: Standard **10** Anfragen pro Minute pro IP (einstellbar **3–50**). Betrifft Login, 2FA, Passwort-Reset und Setup. Hilfreich, wenn sich mehrere Personen über dieselbe Firmen- oder VPN-IP anmelden.
- **Globales API-Limit**: Standard **100** Anfragen pro Minute pro IP (einstellbar **50–1000**). Gilt als Panel-weites Default für die API. Erhöhen, wenn eigene Skripte oder externe Tools die Steuerung häufiger abfragen.
- **Persistenz und Rechte**: Werte liegen als Panel-Settings (`rate_limit_auth`, `rate_limit_global`) in der Datenbank. Lesen und Speichern erfordern `panel.settings.read` bzw. `panel.settings.write`. Ungültige Werte werden serverseitig abgelehnt (HTTP 4xx); zur Laufzeit greifen bei fehlenden oder korrupten Werten immer die dokumentierten Defaults — die Limitierung schaltet sich nie „aus“.
- **Fest bleibende Schutzgrenzen**: Node-Enrollment (Begin/Poll), Singra-Support-Webhook, Zip-/Tar-Bomb-Schutz, Upload-Chunk-Größe und ähnliche technische Limits bleiben bewusst **nicht** im UI konfigurierbar.
- **Cluster-Admin-Rotation unverändert**: Die bestehende Secret-Rotation unter demselben Sicherheitstab bleibt an `system.secrets.rotate` gebunden und ist von den Rate-Limit-Einstellungen getrennt.

---

*Maunting Server Manager v3.0.0 — Safety, Stability and Autonomous Operations.*
