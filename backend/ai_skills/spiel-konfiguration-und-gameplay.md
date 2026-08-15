---
name: Spielkonfiguration und Gameplay anpassen
description: Ändern von Gameplay-Werten wie Multiplikatoren (Ernte, Zähmung, XP, Loot), Servernamen, Passwörtern (ServerPassword, ServerAdminPassword), Schwierigkeit oder Spieleinstellungen. Nutzen bei Bitten wie "mach alles casual", "loot verdoppeln", "Passwort setzen". Nicht nutzen für Fragen zur Serverarchitektur oder Netzwerkdiagnosen.
---

# Spielkonfiguration und Gameplay anpassen

Wenn der Benutzer Einstellungen für das Gameplay (Multiplikatoren, Servername, Passwörter, Schwierigkeitsgrad) anpassen möchte, gehe direkt wie folgt vor:

## 1. Konfigurationsdateien finden

`list_server_files` auf dem Server ausführen. Typische Spieldateien:
- **ARK / ASA**: `ShooterGame/Saved/Config/WindowsServer/GameUserSettings.ini` und `Game.ini`
- **Minecraft**: `server.properties`
- **Palworld**: `Pal/Saved/Config/LinuxServer/PalWorldSettings.ini`
- **DayZ / Rust / CS2**: `server.cfg`, `server.properties`

## 2. Relevante Sektionen und Zeilen lesen

- Mit `search_server_files` nach den Begriffen suchen (z. B. `ServerPassword`, `HarvestAmountMultiplier`, `TamingSpeedMultiplier`, `SessionName`, `motd`).
- Mit `read_config` die passenden Zeilen mit Umgebungs-Offset lesen.

## 3. Werte direkt und präzise patchen

- `propose_config_patch` aufrufen, um die Werte gezielt zu patchen.
- Passwörter (wie `ServerPassword=...` oder `ServerAdminPassword=...`) sind normale INI-Werte und werden direkt geschrieben.
- Wenn der Benutzer im Text mehrere Werte genannt hat ("Servername auf X, Passwort auf Y, Zähmung 4x, Ernte 2x"), bereite alle Patches vor und führe sie aus.
- Keine unnötigen 3-fachen Rückfragen bei klaren Anweisungen im Text.

## 4. Server neu starten, falls erforderlich

- Wenn der Server läuft, weise darauf hin, dass die Konfigurationsänderungen nach einem Neustart aktiv werden (`propose_server_lifecycle` mit `operation: "restart"`).
