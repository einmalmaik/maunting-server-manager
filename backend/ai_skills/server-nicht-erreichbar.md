---
name: Server laeuft, aber niemand kommt drauf
description: Ein Server ist gestartet, aber Spieler koennen sich nicht verbinden — Timeouts, "Server nicht gefunden", leere Serverliste. Nutzen bei jeder Beschwerde ueber Erreichbarkeit, auch wenn der Status "laeuft" zeigt.
---

# Server laeuft, aber niemand kommt drauf

Der haeufigste Supportfall — und fast nie liegt es daran, wofuer die Leute es
halten. Geh in dieser Reihenfolge vor, nicht in einer anderen: jeder Schritt
schliesst Ursachen aus, die den naechsten sonst mehrdeutig machen.

## 1. Laeuft er wirklich?

`read_server_status`. "Laeuft" heisst hier: der Container ist oben. Es heisst
**nicht**, dass die Spielsoftware darin fertig gestartet ist. Ein Minecraft-
oder Valheim-Server braucht nach dem Containerstart oft noch eine Minute, bei
grossen Welten laenger.

## 2. Lauscht ueberhaupt etwas?

`check_server_reachability`. Das ist der aussagekraeftigste Einzelschritt.

Meldet ein Port **frei**, obwohl der Server laeuft, dann lauscht dort nichts.
Der Prozess im Container ist also entweder nicht gestartet, abgestuerzt oder
haengt beim Laden. Weiter bei Schritt 5.

Meldet der Port **belegt**, dann laeuft die Software und nimmt Verbindungen an.
Das Problem liegt dann dazwischen — weiter bei Schritt 3.

## 3. Ist die Bind-IP plausibel?

`read_server_network`. Hier liegt die Ursache ueberraschend oft.

- Gebunden an `127.0.0.1`: nur vom Host selbst erreichbar. Von aussen nie.
- Gebunden an eine Docker-Bruecke wie `172.17.0.1`: nur aus Containern heraus.
- Gebunden an eine private Adresse (`192.168.x.x`, `10.x.x.x`): im lokalen Netz
  erreichbar, aus dem Internet nur mit Portweiterleitung im Router.
- Gebunden an `0.0.0.0`: alle Adressen. Meist richtig.

Passt die Bind-IP nicht zu dem, was der Benutzer erreichen will, schlage mit
`propose_bind_ip_update` eine passende vor und begruende sie mit dem, was du
gemessen hast. Rate keine Adresse — nimm eine aus den Interfaces, die
`read_server_network` gemeldet hat.

## 4. Laesst die Firewall den Port durch?

Steht ebenfalls in `read_server_network`. Achte auf den Unterschied zwischen
"Port ist gesperrt" und "Firewall-Zustand nicht ermittelbar". Ohne UFW kann MSM
darueber nichts sagen — dann sag das auch so, statt eine Vermutung als Befund
zu verkaufen.

## 5. Wenn nichts lauscht: warum nicht?

`read_server_logs`. Such nach:

- **Portkonflikt** — "address already in use", "bind failed". Dann pruefe mit
  `read_server_ports`, welcher andere Server denselben Port belegt.
- **Speicher** — "OutOfMemory", "Killed", plötzlicher Abbruch ohne Fehlerzeile.
  Weiter mit dem Skill zum Arbeitsspeicher.
- **Mods** — Fehler beim Laden direkt nach einer Modaenderung.
- **Beschaedigte Welt oder Konfiguration** — Parserfehler beim Start.

## Was du nicht behaupten darfst

MSM kann **nicht** pruefen, ob ein Port aus dem Internet erreichbar ist. Das
Panel steht hinter derselben Netzwerkgrenze wie der Server; eine Verbindung auf
die eigene oeffentliche Adresse pruefte Hairpin-NAT, nicht die Aussenwelt.

Sag also nie "der Port ist von aussen offen" oder "von aussen dicht". Sag, was
du gemessen hast, und nenne die wahrscheinlichste verbleibende Ursache — bei
einem lauschenden Port hinter einer privaten Adresse ist das fast immer die
fehlende Portweiterleitung im Router des Betreibers.
