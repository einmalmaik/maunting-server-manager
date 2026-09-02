---
name: Server startet nicht oder stürzt sofort ab
description: Ein Server geht nicht an, beendet sich direkt nach dem Start oder bleibt im Startvorgang hängen. Nutzen bei "geht nicht an", "startet und ist gleich wieder aus", Startschleifen und Neuinstallationen, die nicht hochkommen. Nicht nutzen, wenn der Server läuft — dann ist es keine Störung.
---

# Server startet nicht oder stürzt sofort ab

Ein Server, der sich sofort wieder beendet, hat seine Ursache fast immer in den
letzten zwanzig Logzeilen. Fang dort an, nicht bei den Einstellungen.

## 1. Log lesen, von hinten

`read_server_logs`. Die letzte Zeile vor dem Ende ist die wichtigste. Typische
Muster:

- **`Exit code 137`** oder ein Abbruch mitten im Satz: Der Container wurde
  beendet, nicht der Prozess. Das ist praktisch immer der OOM-Killer — weiter
  mit dem Skill zum Arbeitsspeicher.
- **`address already in use`**: Portkonflikt, weiter mit dem Portskill.
- **`Permission denied`** auf einem Pfad: Rechteproblem im Datenverzeichnis,
  oft nach einer Wiederherstellung aus einem Backup.
- **`Unsupported class file major version`** oder ähnliche Versionsfehler:
  falsche Laufzeitversion für die installierte Serverversion oder Mod.
- **Ein Modname in der letzten Zeile**: die Mod bricht den Start ab. Weiter mit
  dem Modskill.
- **Gar kein Log**: Der Container ist nie angelaufen. Dann liegt es am Image,
  am Installationsvorgang oder an der Node.

## 2. Reichen die Ressourcen?

`read_server_capacity` für den Server, `read_node_health` für den Host. Eine
volle Festplatte auf der Node äußert sich als scheinbar zufälliger
Startfehler und ist von außen kaum zu erraten — prüfe sie, bevor du an der
Konfiguration suchst.

## 3. Hat sich kurz vorher etwas geändert?

`read_ai_action_history` und `read_guardian_incidents`. Ein Server, der
gestern lief und heute nicht, hat meist keine neue Ursache, sondern eine neue
Änderung. Frag danach, wenn die Werkzeuge nichts zeigen: eine Modänderung,
ein Update, ein wiederhergestelltes Backup.

## 4. Erst dann Vorschläge

Wenn du die Ursache kennst, schlage genau **eine** Änderung vor und begründe
sie mit der Logzeile, die dich darauf gebracht hat. Ein Vorschlag ohne
Fundstelle ist geraten, und geraten hilft hier niemandem: der nächste Start
dauert wieder Minuten.

Nichts gefunden? Sag das. "Der Log endet ohne Fehlermeldung, das deutet auf
einen Abbruch von außen hin" ist eine brauchbare Auskunft. Eine erfundene
Ursache ist es nicht.
