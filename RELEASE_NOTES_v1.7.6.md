# MSM v1.7.6 — Docker API Compatibility Fix

Hotfix-Release zur Behebung eines `TypeError` beim Aufruf von `container.update()` im Python SDK.

## Highlights

### Ressourcen-Update: Behebung des `TypeError`
In Version 1.7.5 wurde das Live-Update der CPU-Ressourcen auf `nano_cpus` umgestellt, um Konflikte zu vermeiden. Allerdings unterstützt die High-Level-Methode `Container.update()` des Python `docker` SDKs den Parameter `nano_cpus` nicht direkt (dieser wurde vom SDK-Maintainer dort nie in die Signatur aufgenommen, was zu einem `TypeError` führte).

Mit diesem Release greift das Backend bei realen Containern im Live-Update nun direkt auf die Low-Level-Methode `client.api.update_container()` zu. Diese unterstützt `nano_cpus` nativ. Gleichzeitig wird für Testumgebungen und Mocks ein vollkompatibler Fallback aufrechterhalten, sodass das Gesamtsystem stabil und testbar bleibt.

## Geänderte Bereiche

### Backend
- `backend/services/docker_service.py`: Führt Container-Updates bei echten Containern nun über `client.api.update_container` aus.
- `backend/main.py`: App-Version auf `1.7.6` aktualisiert.

### Frontend
- `frontend/package.json`: Version auf `1.7.6` aktualisiert.
- `frontend/package-lock.json`: Synchronisiert.

## Upgrade-Hinweise

```bash
# Update über das offizielle Update-Script (baut Frontend und startet Dienste neu)
sudo bash update.sh --force
```

— Maunting Studios
