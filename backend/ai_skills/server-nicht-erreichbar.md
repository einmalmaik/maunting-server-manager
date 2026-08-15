---
name: Server läuft, aber niemand kommt drauf
description: Ein Server ist gestartet, aber Spieler können sich nicht verbinden — Timeouts, "Server nicht gefunden", leere Serverliste. Nutzen bei jeder Beschwerde über Erreichbarkeit, auch wenn der Status "läuft" zeigt. Nicht nutzen für Fragen zu Spielinhalten oder Einstellungen.
---

# Server läuft, aber niemand kommt drauf

Der häufigste Supportfall — und fast nie liegt es daran, wofür die Leute es
halten. Geh in dieser Reihenfolge vor, nicht in einer anderen: jeder Schritt
schließt Ursachen aus, die den nächsten sonst mehrdeutig machen.

## 1. Läuft er wirklich?

`read_server_status`. "Läuft" heißt hier: der Container ist oben. Es heißt
**nicht**, dass die Spielsoftware darin fertig gestartet ist. Ein Minecraft-
oder Valheim-Server braucht nach dem Containerstart oft noch eine Minute, bei
großen Welten länger.

## 2. Lauscht der Port und antwortet das Spiel?

`check_server_reachability`. Das ist der aussagekräftigste Einzelschritt.

- **Game-Query-Probing**: `check_server_reachability` sendet echte Spielanfragen (wie A2S_INFO für Steam/Source/Unreal-Spiele wie ARK, ASA, Palworld, DayZ oder Minecraft SLP).
- Wenn die Query antwortet (z. B. Servername, Map, Spieler), läuft der Gameserver einwandfrei.
- Meldet ein Port **frei**, obwohl der Server läuft, lauscht dort nichts. Weiter bei Schritt 5.
- Wenn der Port belegt ist, aber keine Query-Antwort kommt: Prüfe Blueprint (`read_blueprint`), Startparameter und Logs (`read_server_logs`).

## 3. Ist die Bind-IP plausibel?

`read_server_network`. Hier liegt die Ursache oft.

- Gebunden an `127.0.0.1`: nur vom Host selbst erreichbar. Von außen nie.
- Gebunden an eine Docker-Brücke wie `172.17.0.1`: nur aus Containern heraus.
- Gebunden an eine private Adresse (`192.168.x.x`, `10.x.x.x`): im lokalen Netz
  erreichbar, aus dem Internet nur mit Portweiterleitung im Router.
- Gebunden an `0.0.0.0`: alle Adressen. Meist richtig.

## 4. Blueprint & Startparameter prüfen

`read_blueprint`. Viele Spiele (wie ARK: Survival Ascended, DayZ, Palworld) verlangen
präzise Startparameter in `runtime.startup` (z. B. `?QueryPort=`, `?Port=`, `?RCONPort=`, `-PublicIPForEpic=`, `-automanagedmods`).
Fehlt ein Query-Port oder ist Multihome falsch gesetzt, bindet der Server zwar den Game-Port, taucht aber nicht im Server-Browser auf.

## 5. Wenn nichts lauscht oder Fehler auftreten

`read_server_logs`. Such nach:

- **Portkonflikt** — "address already in use", "bind failed".
- **Speicher** — "OutOfMemory", "Killed".
- **Mods & Abhängigkeiten** — Fehler beim Herunterladen oder Initialisieren.
- **Beschädigte Konfiguration** — Syntaxfehler in INI/Properties.

## 6. Diagnose und Abhilfe

Kombiniere die Befunde:
- Wenn der Server lokal antwortet, aber auf der öffentlichen IP keine Antwort kommt: Weise den Betreiber auf Portweiterleitung (NAT/Port Forwarding) im Router hin.
- Wenn Startparameter fehlen: Leite mit `propose_blueprint_change` einen korrigierten Blueprint ab und stelle den Server um.
