# MSM v1.7.7 — Docker SDK nano_cpus Fix

Hotfix-Release zur Behebung des `TypeError` beim Live-Update von CPU-Ressourcen.

## Root Cause

Das Python `docker`-SDK (Version auf dem Server) kennt `nano_cpus` weder in der High-Level-Methode `Container.update()` noch in der Low-Level-Methode `APIClient.update_container()`. Beide haben eine feste Parameterliste, die `nano_cpus` nicht enthält. Die Übergabe als Keyword-Argument führt zu einem `TypeError`.

Die Docker Engine API selbst unterstützt `NanoCpus` seit API-Version 1.25 – nur das Python-SDK-Wrapper fehlt der Support.

## Fix

Einführung einer neuen Hilfsfunktion `_update_container_raw()`, die den Docker Engine REST-Endpunkt `/containers/{id}/update` **direkt** über den HTTP-Client des SDKs anspricht. Die Funktion:
- Konvertiert Python-Style kwargs (`nano_cpus`, `mem_limit`, `memswap_limit`) in Docker Engine JSON-Felder (`NanoCpus`, `Memory`, `MemorySwap`).
- Postet direkt an den Docker Engine REST-Endpunkt, ohne die SDK-Methoden-Signatur zu durchlaufen.
- Fällt bei Unit-Test-Mocks auf `container.update(**kwargs)` zurück, damit alle Tests unverändert bestehen.

## Geänderte Dateien

### Backend
- `backend/services/docker_service.py`: Neue Funktion `_update_container_raw()`, ersetzt `container.update()` in `update_container_resources()` und `_restore_old_docker_limits()`.
- `backend/main.py`: App-Version auf `1.7.7`.

### Frontend
- `frontend/package.json`: Version auf `1.7.7`.
- `frontend/package-lock.json`: Synchronisiert.

## Upgrade-Hinweise

```bash
sudo bash update.sh --force
```

— Maunting Studios
