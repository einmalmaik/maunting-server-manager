# MSM v2.0.0 — Die Multi-Node-Revolution 🚀

Willkommen im neuen Zeitalter des Maunting Server Managers! Mit der Version 2.0.0 brechen wir die Grenzen eines einzelnen Servers auf. MSM transformiert sich von einer All-in-One-Anwendung zu einem vollwertigen, hochmodernen **verteilten System**. Verwalte, skaliere und sichere deine Gameserver-Infrastruktur über beliebig viele Server hinweg — nahtlos gesteuert aus einem einzigen, zentralen Control-Panel.

Dieses Major-Update bringt neben der fundamentalen Multi-Node-Architektur auch massive Verbesserungen bei Performance, Ausfallsicherheit, Backups und Benutzeroberfläche.

---

## Die Highlights

### 🌐 Multi-Node-Architektur (Verteilte Server-Power)
Bisher liefen das Panel und all deine Gameserver zwingend auf demselben physischen Host. Ab sofort trennen wir die Steuerung von der Ausführung:
* **Zentrales Control-Panel (Control Plane):** Deine Web-UI und das Haupt-Backend laufen an einem Ort.
* **Leichte Worker-Nodes:** Auf jedem zusätzlichen Server läuft der neu entwickelte, extrem schlanke und gehärtete **MSM Agent**. Er steuert die lokalen Docker-Container, überwacht Ressourcen und wickelt Datei-Operationen ab.
* **Echtzeit-Telemetrie:** CPU- und RAM-Auslastungen aller Nodes werden asynchron und parallel erfasst. Ein intelligentes Caching sorgt dafür, dass das Panel selbst bei Dutzenden verbundenen Servern absolut flüssig bleibt.

### 🔌 Magisches Node-Enrollment (In 2 Minuten online)
Das Hinzufügen eines neuen Servers war noch nie so einfach und sicher:
1. Klicke im Panel unter **Nodes** auf **Node hinzufügen**.
2. Kopiere den dort generierten, einweg-verschlüsselten Installationsbefehl.
3. Führe den Befehl als Root auf deinem neuen Server aus.
4. Der Installer erledigt den Rest: Er richtet ein rootless Docker-Setup ein, generiert lokale Zertifikate und startet den Dienst.
5. Vergleiche den kurzen Bestätigungscode auf dem Bildschirm mit dem Panel und schalte den Node mit einem Klick frei.
* **Sicherheit pur:** Keine Passwörter oder sensitiven Tokens in URLs oder Logdateien. Das Panel speichert Verbindungstokens kryptografisch verschlüsselt ab (DIS-Storage).

### 🗄️ Managed Postgres auf den Nodes
Schluss mit SQLite-Limitierungen! MSM setzt nun konsequent auf **PostgreSQL** für maximale Transaktionssicherheit.
* Auf jedem Node wird vollautomatisch ein isolierter PostgreSQL-Container (`msm-postgres`) betrieben.
* Legt ein Benutzer eine Datenbank für seinen Gameserver an, provisioniert der MSM-Agent diese in Sekundenschnelle auf dem Ziel-Node.
* Datenbank-Zugangsdaten werden niemals im Klartext auf den Nodes gespeichert. Backups und Restores nutzen native PostgreSQL-Mechanismen (Dumps über stdin/stdout), um Ownership und Berechtigungen fehlerfrei zu erhalten.

### ☁️ Dezentrales S3-Backup-Streaming (Direkt in die Cloud)
Backups belasten ab sofort weder den Festplattenplatz noch die Bandbreite deines Haupt-Panels:
* Nodes komprimieren und verschlüsseln Server-Backups (AES-GCM) in Echtzeit und streamen sie **direkt** in deinen S3-Speicher.
* S3-Credentials und Verschlüsselungsschlüssel existieren nur im flüchtigen RAM des Agenten während der Dauer des Uploads — sie werden niemals auf die Festplatte des Nodes geschrieben.

### 🛠️ Interaktiver Migrationsassistent & Split-Hosting
Möchtest du einen bestehenden Gameserver auf einen anderen Node umziehen? Oder deine bisherige All-in-One-Installation in ein separates Frontend und Backend aufteilen?
* Das neue Tool `/opt/msm/helper-scripts/migrate-panel-components.sh` leitet dich interaktiv durch alle Schritte.
* Es prüft Speicherplatz und Ports auf dem Ziel, kopiert Serverdateien (inklusive Saves, Mods und Backups), migriert PostgreSQL-Datenbanken und schaltet die Zuordnung erst um, wenn alle Health-Checks grün sind.
* **Frontend-Entkopplung:** Das Frontend ist nun komplett vom Backend getrennt und „Vercel-Ready“. Du kannst die UI statisch hosten (z. B. auf Vercel, Cloudflare) und das API-Backend auf deinem Server laufen lassen — inklusive vollem Support für CORS, getrennte Cookie-Domains und sichere CSRF-Tokens.

### 🛡️ Generischer CAPTCHA-Schutz
Sichere dein Panel wirksam gegen Brute-Force-Angriffe und automatisierte Registrierungen ab.
* Im Einstellungs-Tab **CAPTCHA** kannst du mit wenigen Klicks **Cloudflare Turnstile**, **hCaptcha** oder **Google reCAPTCHA** aktivieren.
* Der Schutz greift sofort auf der Login-Seite, bei der Registrierung sowie beim Zurücksetzen von Passwörtern.

---

## Bugfixes & Detailverbesserungen

* **Namens-Update:** Die Menü-Bezeichnung „Infrastructure Control“ wurde für mehr Klarheit in **Server Manager** geändert.
* **UI-Vereinheitlichung:** Die Suchfelder in der Node-Verwaltung wurden optisch an das MSM Design-System angepasst (`msm-input`).
* **Zuverlässige CPU/RAM-Bars:** Fehlerhafte Prozentberechnungen und UI-Flackern bei den Ressourcenbalken gehören der Vergangenheit an.
* **WebSocket TLS-Pinning:** Beim Tunneln der Server-Konsole zu einem Remote-Node wird nun das TLS-Zertifikat des Nodes korrekt über einen gepinnten SSL-Context validiert. Konsolenverbindungen brechen nicht mehr unbegründet ab.
* **Robustere Webhooks:** Webhook-Signaturen des Discord-Bots Singra werden nun zeitzonenunabhängig und mit stabiler URL-Formatierung validiert.
* **Installer-Härtung:** Der Installationsprozess toleriert nun Netzwerk-Besonderheiten unter WSL2, aktualisierte Caddy-Paketquellen-Labels und führt Rechteanpassungen (`chown`) vor der venv-Erstellung fehlerfrei aus.
* **Sprachauswahl gesäubert:** Um das Interface clean zu halten, wurden die redundanten Sprachumschalter aus der Topbar und dem Login entfernt. Die Spracheinstellung wird nun zentral und konsistent im Profil/Einstellungen-Tab gesteuert.

---

## Upgrade-Anleitung (v1.8.0 ➡️ v2.0.0)

> [!WARNING]
> Da es sich um ein Major-Update mit weitreichenden Datenbankänderungen handelt, erstelle bitte vor dem Upgrade ein Backup deiner bestehenden Datenbank (`backend/msm.db`).

Führe den folgenden Befehl auf deinem Panel-Server aus, um das Update zu starten:

```bash
sudo bash update.sh --force
```

Das Skript lädt das neue v2.0.0 Release, führt die automatischen Datenbankmigrationen (Modelle, TLS-Fingerprints) aus und startet die Dienste neu.

Führe nach dem Update im Browser einen **Hard-Reload** durch (z. B. `Strg + F5` oder Cache leeren), um sicherzustellen, dass die neue UI-Struktur und die Einstellungs-Tabs geladen werden.

---

## Rollback-Anleitung (Zurück zu v1.8.0)

Sollte wider Erwarten etwas nicht funktionieren, kannst du jederzeit auf den alten Stand zurückrollen:

1. Stoppe die MSM-Dienste:
   ```bash
   sudo systemctl stop msm
   ```
2. Stelle deine gesicherte Datenbank `msm.db` im Verzeichnis `backend/` wieder her.
3. Wechsle das Code-Verzeichnis zurück auf die Version v1.8.0 (oder nutze dein vorheriges Dateibackup):
   ```bash
   git checkout v1.8.0
   ```
4. Starte die Dienste wieder:
   ```bash
   sudo systemctl start msm
   ```

---

— **Maunting Studios**
