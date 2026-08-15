---
name: Spielkonfiguration und Gameplay anpassen
description: Ändern von Werten in den Konfigurationsdateien eines Servers — Multiplikatoren (Ernte, Zähmung, XP, Loot), Servername, Schwierigkeit, MOTD, Slots. Nutzen bei Bitten wie "mach alles casual", "loot verdoppeln", "Servername ändern" und wenn ein Passwort gesetzt werden soll. Nicht nutzen für Startparameter, Version oder Image — die stehen im Blueprint, nicht in den Dateien.
---

# Spielkonfiguration und Gameplay anpassen

Der Weg ist immer derselbe, egal um welches Spiel es geht: finden, die Umgebung
lesen, die einzelne Stelle patchen. Rate keine Dateinamen — jedes Spiel legt
seine Einstellungen woanders ab, und ein Fehlversuch sagt dir nichts darüber,
ob es die Datei gibt.

## 1. Datei finden statt raten

`list_server_files` zeigt, was tatsächlich da ist. Weißt du den Namen der
Einstellung, aber nicht die Datei, ist `search_server_files` der kürzere Weg:
es findet den Begriff über alle Textdateien des Servers.

## 2. Die Umgebung der Stelle lesen

`read_config` mit `offset` auf die gefundene Zeile. Eine Spielkonfiguration hat
tausende Zeilen; du brauchst nur die Umgebung der Stelle, die du änderst — und
`total_lines` sagt dir, wo du bist.

## 3. Gezielt patchen

`propose_config_patch`. In den `find` gehört so viel Umgebung, dass er in der
ganzen Datei genau einmal vorkommt — die ganze Zeile oder das umschließende
Element, nicht nur der Wert. Wird der Vorschlag als nicht eindeutig abgewiesen,
nimm mehr Umgebung dazu.

Hat der Benutzer mehrere Werte in einem Satz genannt ("Servername auf X, Zähmung
4x, Ernte 2x"), gehören sie in **einen** Vorschlag. Frag dazu nicht noch einmal
mit `ask_user` nach — er hat es bereits gesagt.

## 4. Passwörter kannst du nicht setzen

Das ist keine Vorsicht, sondern eine Schranke im Backend, und sie greift
ausnahmslos: Ein Vorschlag, dessen `find` oder `replace` ein Zugangsdatenmuster
enthält (`ServerPassword`, `ServerAdminPassword`, RCON- oder Datenbankpasswörter
in jeder Schreibweise), wird abgewiesen. Und eine Datei, die bereits ein solches
Feld trägt, lässt sich nicht mehr als Ganzes ersetzen.

Was daraus folgt:

- Sag dem Benutzer **einmal**, dass er genau diesen Wert selbst im Dateimanager
  einträgt, und nenne ihm Datei und Zeile. Versuch es nicht umformuliert erneut —
  jeder weitere Versuch endet bei derselben Absage.
- Die übrigen Werte derselben Datei änderst du ganz normal weiter. Ein Passwort
  drei Zeilen weiter stört einen Patch an anderer Stelle nicht.
- Den Wert selbst schreibst du nirgends hin, auch nicht in deine Antwort. Was du
  gelesen hast, war ohnehin nur ein Platzhalter.

## 5. Neustart nicht vergessen

Läuft der Server, wirken die meisten Konfigurationsänderungen erst nach einem
Neustart. Sag das dazu und biete ihn mit `propose_server_lifecycle`
(`operation: "restart"`) an, statt Vollzug zu melden.
