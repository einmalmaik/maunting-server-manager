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
