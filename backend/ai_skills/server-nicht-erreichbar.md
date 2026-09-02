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
**nicht**, dass die Spielsoftware darin fertig gestartet ist. Ein Server mit
großer Welt braucht nach dem Containerstart oft noch eine Minute oder länger.

## 2. Lauscht etwas — und antwortet das Spiel?

`check_server_reachability`. Das ist der aussagekräftigste Einzelschritt, und er
liefert zwei verschiedene Befunde, die man nicht verwechseln darf.

**Die Ports.** Meldet ein Port **frei**, obwohl der Server läuft, dann lauscht
dort nichts. Der Prozess im Container ist also entweder nicht gestartet,
abgestürzt oder hängt beim Laden. Weiter bei Schritt 5. Meldet der Port
**belegt**, dann läuft die Software und nimmt Verbindungen an. Das Problem liegt
dann dazwischen — weiter bei Schritt 3.

**Die Anwendungsprobe.** Ob und welche Spielabfrage für diesen Server gilt, steht
im Blueprint; gemessen hat sie der Guardian auf der Node, auf der der Server
liegt — nicht das Panel. Vier Zustände, drei Bedeutungen:

- `answering`: das Spiel selbst antwortet. Der Dienst ist oben, such die Ursache
  ab Schritt 3.
- `not_answering`: der Port lauscht, die Abfrage bleibt aus. Das ist ein Befund.
  Lies Blueprint (`read_blueprint`) und Logs (`read_server_logs`) — häufig fehlt
  ein Abfrage-Port im Startbefehl oder die Bindung erfasst nur einen Teil der
  Adressen.
- `not_declared`: für diesen Blueprint ist gar keine Probe hinterlegt. Eine
  ausbleibende Antwort ist dann **kein** Befund, sondern eine fehlende Messung —
  ein Titel mit eigener Engine spricht kein Protokoll, das MSM abfragen könnte.
  Sag das so und arbeite mit Ports, Bind-IP und Logs weiter.
- `no_measurement`: es liegt noch keine Messung vor, etwa kurz nach dem Start.
  Warte einen Moment und sieh erneut nach, statt daraus etwas zu schließen.

## 3. Ist die Bind-IP plausibel?

`read_server_network`. Hier liegt die Ursache überraschend oft.

- Gebunden an `127.0.0.1`: nur vom Host selbst erreichbar. Von außen nie.
- Gebunden an eine Docker-Brücke wie `172.17.0.1`: nur aus Containern heraus.
- Gebunden an eine private Adresse (`192.168.x.x`, `10.x.x.x`): im lokalen Netz
  erreichbar, aus dem Internet nur mit Portweiterleitung im Router.
- Gebunden an `0.0.0.0`: alle Adressen. Meist richtig.

Passt die Bind-IP nicht zu dem, was der Benutzer erreichen will, schlage mit
`propose_bind_ip_update` eine passende vor und begründe sie mit dem, was du
gemessen hast. Rate keine Adresse — nimm eine aus den Interfaces, die
`read_server_network` gemeldet hat.

## 4. Lässt die Firewall den Port durch?

Steht ebenfalls in `read_server_network`. Achte auf den Unterschied zwischen
"Port ist gesperrt" und "Firewall-Zustand nicht ermittelbar". Ohne UFW kann MSM
darüber nichts sagen — dann sag das auch so, statt eine Vermutung als Befund
zu verkaufen.

## 5. Wenn nichts lauscht: warum nicht?

`read_server_logs`. Such nach:

- **Portkonflikt** — "address already in use", "bind failed". Dann prüfe mit
  `read_server_ports`, welcher andere Server denselben Port belegt.
- **Speicher** — "OutOfMemory", "Killed", plötzlicher Abbruch ohne Fehlerzeile.
  Weiter mit dem Skill zum Arbeitsspeicher.
- **Mods** — Fehler beim Laden direkt nach einer Modänderung.
- **Beschädigte Welt oder Konfiguration** — Parserfehler beim Start.
- **Startbefehl** — fehlt ein Port- oder Abfrageparameter, steht er im Blueprint
  und nicht in den Serverdateien. Weiter mit dem Skill zu Startparametern.

## Was du nicht behaupten darfst

MSM kann **nicht** prüfen, ob ein Port aus dem Internet erreichbar ist. Das
Panel steht hinter derselben Netzwerkgrenze wie der Server; eine Verbindung auf
die eigene öffentliche Adresse prüfte Hairpin-NAT, nicht die Außenwelt.

Sag also nie "der Port ist von außen offen" oder "von außen dicht". Sag, was
du gemessen hast, und nenne die wahrscheinlichste verbleibende Ursache — bei
einem lauschenden Port hinter einer privaten Adresse ist das fast immer die
fehlende Portweiterleitung im Router des Betreibers.

Eine fehlende Probe ist kein solcher Grund. `not_declared` heißt, dass MSM
nichts gemessen hat, nicht dass etwas kaputt ist — wer den Betreiber deswegen
in seinen Router schickt, lässt ihn an einer Stelle suchen, an der nichts ist.
