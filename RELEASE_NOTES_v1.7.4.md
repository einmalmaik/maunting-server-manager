# MSM v1.7.4 — Docker-Update Log-Verbesserung

Hotfix-Release zur Verbesserung des Loggings bei fehlgeschlagenen live Docker-Ressourcen-Updates.

## Highlights

### Logging: Exception-Verwertung bei Ressourcen-Updates
Schlägt das Ändern von CPU- oder RAM-Limits eines laufenden Docker-Containers fehl, loggt das System nun auch die zugrundeliegende Fehlermeldung der Docker-Engine (z. B. fehlende Kernel-Unterstützung für Speicherlimits oder Cgroup-v2-Rechteprobleme). Dies ermöglicht eine schnelle Diagnose von Systemen mit Rootless-Docker-Einschränkungen oder inkompatiblen Kerneln.

## Geänderte Bereiche

### Backend
- `backend/services/docker_service.py`: Loggt jetzt auch die Exception in `update_container_resources()`.
- `backend/main.py`: App-Version auf `1.7.4` aktualisiert.

### Frontend
- `frontend/package.json`: Version auf `1.7.4` aktualisiert.

## Upgrade-Hinweise

```bash
# Empfohlen: Update über das offizielle Update-Script (baut Frontend und startet Dienste neu)
sudo bash update.sh --force
```

— Maunting Studios
