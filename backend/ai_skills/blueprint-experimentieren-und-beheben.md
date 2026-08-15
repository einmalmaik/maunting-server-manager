---
name: Blueprint experimentieren, anpassen und bereinigen
description: Beheben von Startparameter-Problemen, Ausprobieren neuer Startflags oder Testen von Umgebungs- und Image-Änderungen in Blueprints. Nutzen wenn ein Blueprint angepasst, getestet oder ein temporärer Test-Blueprint nach Abschluss gelöscht werden soll. Nicht nutzen für reine Statusabfragen oder einfache Konfigurationsänderungen.
---

# Blueprint experimentieren, anpassen und bereinigen

Blueprints steuern Container-Image, Startparameter (`runtime.startup`), Umgebungsvariablen (`runtime.env`) und Ports.

## 1. Blueprint analysieren

- Mit `read_blueprint` den Blueprint des Servers oder aus `list_blueprints` einsehen.
- Startbefehle in `runtime.startup` prüfen (z. B. fehlende Parameter wie `-PublicIPForEpic=`, `?QueryPort=`, `?Port=`, `-automanagedmods`).

## 2. Test-Blueprint ableiten

- Native Blueprints (`origin: native`) sind schreibgeschützt.
- Mit `propose_blueprint_change` einen abgeleiteten Community-Blueprint mit den Test-Anpassungen erstellen (z. B. `changes: {"runtime.startup": "..."}`).

## 3. Server auf neuen Blueprint umstellen und testen

- Mit `propose_server_blueprint_switch` den Server auf den neuen Blueprint umstellen.
- Server starten (`propose_server_lifecycle`), Status und Erreichbarkeit mit `check_server_reachability` und Logs mit `read_server_logs` prüfen.

## 4. Test-Blueprints nach Abschluss bereinigen

- Wenn ein temporärer Test-Blueprint nicht mehr benötigt wird oder ein Fehler behoben wurde:
- Lösche den Test-Blueprint mit `propose_blueprint_delete` (`blueprint_id: "..."`).
- Native Blueprints können nicht gelöscht werden.
