# Phase 5: Agent-Installer & Produktionsreife

Dieses Dokument beschreibt die Maßnahmen zur Absicherung und Überwachung der Node-Agenten im produktiven Betrieb sowie die Bereitstellung eines automatisierten Installers.

---

## 1. TLS-Absicherung (Fingerprint-Pinning)

Um den `MSM_AGENT_TOKEN` bei der Übertragung über das Internet vor Man-in-the-Middle-Angriffen zu schützen, ist **TLS zwingend erforderlich**.

### Self-Signed Zertifikate & Pinning
Aus Kostengründen (keine eigenen Domains/Zertifikate für jeden Node notwendig) wird ein Self-signed TLS-Zertifikat verwendet:
1. **Generierung**: Der Agent-Installer generiert bei der Einrichtung ein selbstsigniertes Zertifikat (RSA 4096 oder Ed25519) und berechnet den SHA-256-Fingerprint.
2. **Hinterlegung**: Beim Hinzufügen des Nodes im Panel trägt der Administrator den Fingerprint ein. Das Feld wird im `Node`-Modell gespeichert.
3. **Validierung (Pinning)**:
   - Das Panel baut Verbindungen zum Agenten über ein angepasstes SSL-Context-Objekt auf.
   - Das Standard-Zertifikats-Check (CA-Chain) wird übersprungen.
   - Stattdessen wird der SHA-256-Fingerprint des vom Agenten gelieferten Zertifikats live berechnet und mit dem in der DB hinterlegten Wert abgeglichen. Bei Abweichung wird die Verbindung sofort abgebrochen.

---

## 2. Heartbeat & Monitoring

- Im Panel-Backend (`scheduler_service.py`) wird ein periodischer Hintergrund-Job registriert (z.B. alle 60 Sekunden).
- Der Job ruft `/health` auf jedem registrierten Node auf.
- Bei Erfolg wird `nodes.status = "online"` und `nodes.last_heartbeat = datetime.now()` gesetzt.
- Schlägt der Aufruf fehl (Timeout oder Connect-Error), wird der Status auf `"offline"` gesetzt.

### Graceful Degradation
- Wenn ein Node offline ist, dürfen die zugehörigen Server im Dashboard nicht verschwinden. Sie werden als `"offline"` oder `"node_unreachable"` markiert.
- Start-, Stopp- und Dateioperationen für Server auf offline Nodes werden im Frontend blockiert und im Backend mit einer klaren Fehlermeldung abgelehnt.

---

## 3. Automatisierter Agent-Installer (`scripts/install-agent.sh`)

Ein robustes Bash-Skript für den Node-Server:
1. **System-Check**: Prüft auf Ubuntu/Debian, Root-Rechte und Python 3.11+.
2. **Rootless Docker**: Installiert und konfiguriert rootless Docker für den `msm`-Systembenutzer, falls noch nicht vorhanden.
3. **Agent-Deployment**:
   - Legt das Verzeichnis `/opt/msm-agent/` an.
   - Kopiert die Agent-Dateien dorthin.
   - Erstellt ein Python Virtualenv und installiert Dependencies aus `requirements.txt`.
4. **Zertifikats-Generierung**: Generiert den privaten Schlüssel und das selbstsignierte Zertifikat für den Webserver.
5. **Config**: Generiert einen sicheren Token und schreibt die `/opt/msm-agent/.env`.
6. **systemd Integration**: Registriert und startet die systemd-Unit `msm-agent.service`.
7. **Abschluss-Ausgabe**:
   - Zeigt die IP/Port-Konfiguration des Agenten.
   - Zeigt den generierten `MSM_AGENT_TOKEN`.
   - Zeigt den SHA-256-Fingerprint des Zertifikats.

---

## 4. Test- und Verifizierungsschritte

1. **Remote-Installation**:
   - Führe das Install-Skript auf einem Test-VPS (oder einer VM) aus.
   - Kopiere die angezeigten Daten (URL, Token, Fingerprint).
2. **Verbindungstest**:
   - Trage den Node im Panel ein.
   - Verifiziere, dass das Panel den Node als "online" erkennt.
   - Ändere testweise den Fingerprint im Panel und verifiziere, dass Verbindungen daraufhin blockiert werden (Pinning-Erfolg).
