# Release Notes — Maunting Server Manager v3.0.0 (Major Release)

---

## 🛡️ Guardian Autonomous Engine

Der Maunting Server Manager v3.0.0 führt die **Guardian Autonomous Engine** ein — ein autonomes, ausfallsicheres Monitoring- und Self-Healing-System für Spiele- und Anwendungsserver. Die Engine überwacht Server in Echtzeit auf Agent- und Backend-Ebene, erkennt Abstürze oder fehlerhafte Zustände selbstständig und führt automatisch Reparaturmaßnahmen durch.

### Kernfunktionalitäten der Guardian Engine

#### 1. Autonomes Self-Healing & Recovery Ladder
- **Echtzeit-Probes**: Überwachung via frei konfigurierbarer Health-Probes (`HTTP`, `TCP`, `UDP`, `RCON`, `Source Query` und `Process Check`).
- **Recovery Ladder**: Stufenweise Wiederherstellung bei Ausfällen (z. B. Sanfter Neustart -> Hard Reboot -> SIGTERM/Quarantäne).
- **Quarantäne-Modus**: Verhindert endlose Neustart-Schleifen (Crash Loops) und Ressourcen-Fresser. Wenn ein Server trotz mehrerer Reparaturversuche nicht stabil läuft, versetzt ihn die Engine automatisch in den Quarantäne-Zustand und benachrichtigt die Administratoren.
- **1-Click Vorfalls-Auflösung**: Im Dashboard (neuer Tab **Guardian**) sehen Betreiber alle aktiven und vergangenen Vorfälle inkl. Ausführungs-Logs. Ein Klick auf *Quarantäne aufheben / Vorfall beheben* setzt den Server-Status zurück und hebt die Quarantäne auf.

#### 2. Intelligente Recovery Leases (Aussetzung bei Admin-Aktionen)
- Die Guardian Engine unterscheidet strikt zwischen einem unerwarteten Server-Absturz und beabsichtigten Aktionen des Administrators.
- Bei manuellen Starts, Stopps, Updates oder Konfigurationsänderungen werden **Recovery Leases** automatisch pausiert, sodass der Guardian niemals störend in administrative Eingriffe eingreift.

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

### Systemd & Sicherheit
- **Agent-State Berechtigungen**: Anpassung der Systemd-Unit für den MSM-Agenten, sodass Schreibzugriffe auf `/var/lib/msm-agent` auch unter `ProtectSystem=strict` sicher gewährleistet sind.
- **Lokalisierung (i18n)**: Neue Fehlerübersetzungen für Node-Client-Verbindungsfehler und vereinheitlichte Fehlermeldungen im gesamten Frontend.

---

*Maunting Server Manager v3.0.0 — Safety, Stability and Autonomous Operations.*
