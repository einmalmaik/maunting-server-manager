# MSM v1.7.5 — Docker Live-Update Fix

Hotfix-Release zur Behebung des Konflikts beim Aktualisieren von CPU-Limits auf Docker.

## Highlights

### Ressourcen-Update: Umstellung auf `nano_cpus`
Bisher wurden Container mit der Option `nano_cpus` gestartet, ein anschließendes Live-Update versuchte jedoch, die CPU-Limits über die älteren Optionen `cpu_period` und `cpu_quota` zu aktualisieren. Dies führte bei Docker-Engines mit gesetzten `NanoCPUs` zu einem Konfliktfehler (`Conflicting options: CPU Period cannot be updated as NanoCPUs has already been set`). 

Mit diesem Release wird auch das Live-Update konsequent auf `nano_cpus` umgestellt, wodurch dieser Fehler behoben ist und CPU-Limits nun reibungslos im laufenden Betrieb geändert werden können.

## Geänderte Bereiche

### Backend
- `backend/services/docker_service.py`: Führt Updates und Rollbacks nun ausschließlich über `nano_cpus` (HostConfig-Key `NanoCpus`) durch.
- `backend/tests/test_docker_service.py`: Test-Suiten auf `nano_cpus` angepasst.
- `backend/main.py`: App-Version auf `1.7.5` aktualisiert.

### Frontend
- `frontend/package.json`: Version auf `1.7.5` aktualisiert.
- `frontend/package-lock.json`: Synchronisiert.

## Upgrade-Hinweise

```bash
# Update über das offizielle Update-Script (baut Frontend und startet Dienste neu)
sudo bash update.sh --force
```

— Maunting Studios
