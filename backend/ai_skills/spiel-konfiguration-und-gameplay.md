---
name: Spielkonfiguration und Gameplay anpassen
description: Ändern von Werten in den Konfigurationsdateien eines Servers — Multiplikatoren (Ernte, Zähmung, XP, Loot), Servername, Schwierigkeit, MOTD, Slots. Nutzen bei Bitten wie "mach alles casual", "loot verdoppeln", "Servername ändern" und wenn ein Passwort gesetzt werden soll. Nicht nutzen für Startparameter, Version oder Image — die stehen im Blueprint, nicht in den Dateien.
---

# Spielkonfiguration und Gameplay anpassen

Der Weg ist immer derselbe, egal um welches Spiel es geht: finden, die Umgebung
lesen, den Wert setzen. Rate keine Dateinamen — jedes Spiel legt seine
Einstellungen woanders ab, und ein Fehlversuch sagt dir nichts darüber, ob es
die Datei gibt.

## 1. Datei finden statt raten

`list_server_files` zeigt, was tatsächlich da ist. Weißt du den Namen der
Einstellung, aber nicht die Datei, ist `search_server_files` der kürzere Weg:
es findet den Begriff über alle Textdateien des Servers.

## 2. Die Umgebung der Stelle lesen

`read_config` mit `offset` auf die gefundene Zeile. Eine Spielkonfiguration hat
tausende Zeilen; du brauchst nur die Umgebung der Stelle, die du änderst — und
`total_lines` sagt dir, wo du bist. Von dort stammt auch die
`expected_revision`, die du zum Schreiben brauchst.

## 3. Werte setzen

Für INI-artige Dateien (`.ini`, `.cfg`, `.conf`) ist `propose_config_set` der
Weg: du nennst Sektion, Schlüssel und Wert, statt Text zu suchen. Das ist bei
Spieleinstellungen fast immer der Fall.

Zwei Fehler sind damit ausgeschlossen, die beide gemessen aufgetreten sind: ein
zweiter gleichnamiger Abschnitt am Dateiende (das Spiel liest nur den ersten,
die Werte sind dann richtig und wirkungslos) und ein Suchtext, der an
Windows-Zeilenenden scheitert.

Für XML, JSON und alles andere bleibt `propose_config_patch`. Dort muss im
`find` so viel Umgebung stehen, dass er in der ganzen Datei genau einmal
vorkommt — die ganze Zeile oder das umschließende Element, nicht nur der Wert.
Wird der Vorschlag als nicht eindeutig abgewiesen, nimm mehr Umgebung dazu.

Hat der Benutzer mehrere Werte in einem Satz genannt ("Servername auf X, Zähmung
4x, Ernte 2x"), gehören sie in **einen** Vorschlag. Frag dazu nicht noch einmal
mit `ask_user` nach — er hat es bereits gesagt.

## 4. Fehlende Einträge legst du an

Steht die Einstellung noch nicht in der Datei, trägst du sie ein. Bei
Spielkonfigurationen ist das der Regelfall: die Dateien enthalten meist nur,
was einmal verändert wurde, und der Rest läuft auf Standardwerten.

Ein fehlender Schlüssel ist deshalb kein Hindernis und kein Grund, dem Benutzer
abzusagen. `propose_config_set` legt die Sektion mit an, wenn auch sie fehlt.

## 5. Ein laufender Server hindert dich nicht

Du kannst die Konfiguration ändern, während der Server läuft. Sag dazu, dass es
mit dem nächsten Neustart wirkt, und biete ihn mit `propose_server_lifecycle`
(`operation: "restart"`) an — aber verlange nicht, dass der Benutzer den Server
vorher stoppt, und lehne die Änderung nicht deswegen ab.

Manche Spiele — ARK ist das bekannteste — halten ihre Einstellungen im Speicher
und schreiben die Konfigurationsdatei beim Beenden oder beim Autosave komplett
neu. Eine Änderung an der Datei allein wäre dort nach kurzer Zeit wieder weg.
Genau deshalb hinterlegt `propose_config_set` den Wert zusätzlich dauerhaft: er
wird vor **jedem** Start erneut geschrieben. Du musst dafür nicht wissen, wie
das jeweilige Spiel damit umgeht.

Die einzige Ausnahme ist der Blueprint-Wechsel: der löscht das gesamte
Serververzeichnis und verlangt einen gestoppten Server. Das ist eine andere
Sache als eine Konfigurationsänderung.

## 6. Passwörter kannst du nicht setzen

Das ist keine Vorsicht, sondern eine Schranke im Backend, und sie greift
ausnahmslos: Ein Vorschlag, der ein Zugangsdatenmuster enthält
(`ServerPassword`, `ServerAdminPassword`, RCON- oder Datenbankpasswörter in
jeder Schreibweise), wird abgewiesen. Und eine Datei, die bereits ein solches
Feld trägt, lässt sich nicht mehr als Ganzes ersetzen.

Was daraus folgt:

- Sag dem Benutzer **einmal**, dass er genau diesen Wert selbst im Dateimanager
  einträgt, und nenne ihm Datei und Zeile. Versuch es nicht umformuliert erneut —
  jeder weitere Versuch endet bei derselben Absage.
- Die übrigen Werte derselben Datei änderst du ganz normal weiter. Ein Passwort
  drei Zeilen weiter stört eine Änderung an anderer Stelle nicht.
- Den Wert selbst schreibst du nirgends hin, auch nicht in deine Antwort. Was du
  gelesen hast, war ohnehin nur ein Platzhalter.
