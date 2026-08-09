---
name: Zu wenig Arbeitsspeicher, OOM-Kill erkennen
description: Ein Server wird ohne Fehlermeldung beendet, stuerzt unter Last ab oder laeuft nach einiger Zeit immer langsamer. Nutzen bei Exit code 137, "Killed", wiederkehrenden Abstuerzen zur Stosszeit und Fragen nach der richtigen RAM-Groesse.
---

# Zu wenig Arbeitsspeicher

Der OOM-Kill ist der taeuschendste Fehler im Serverbetrieb: es gibt keine
Fehlermeldung. Der Kernel beendet den Prozess, und das Log hoert einfach auf.
Wer danach im Log nach einer Ursache sucht, findet keine — und verdaechtigt
dann Mods, Netzwerk oder die Spielsoftware.

## Woran du ihn erkennst

Drei Anzeichen, zusammen praktisch beweisend:

1. `read_server_logs` endet **mitten im Betrieb**, ohne Abschiedszeile. Kein
   "Shutting down", kein Stacktrace, kein Fehler.
2. `read_server_status` meldet `Exit code 137`. Das ist 128 + 9, also SIGKILL —
   der Prozess wurde beendet, nicht gebeten.
3. `read_server_capacity` zeigt eine RAM-Grenze nahe am tatsaechlichen
   Verbrauch kurz vor dem Ende.

Fehlt Punkt 2, reichen 1 und 3 fuer eine begruendete Vermutung — sag dann auch
"vermutlich", nicht "sicher".

## Wieviel Speicher wirklich noetig ist

Faustwerte fuer die haeufigen Spiele. Sie sind Startpunkte, keine Wahrheiten —
Mods und Spielerzahl verschieben alles nach oben:

| Spiel | Ohne Mods | Mit Mods |
|---|---|---|
| Minecraft (Vanilla) | 2–4 GB | 6–8 GB, modpackabhaengig deutlich mehr |
| Valheim | 4 GB | 6–8 GB |
| Rust | 8 GB | 12 GB und mehr, stark kartengroessenabhaengig |
| ARK | 8 GB | 12–16 GB |
| DayZ | 4 GB | 6–8 GB |
| Palworld | 8 GB | 16 GB |

Rechne bei Minecraft-Modpacks nicht mit dem Wert des Packs, sondern mit dem
Wert plus etwa ein Drittel: die Angaben der Packersteller gelten fuer den
Einzelspielerbetrieb.

## Was du vorschlaegst

Pruefe zuerst mit `read_node_health`, ob auf dem Host ueberhaupt Luft ist.
Einem Server mehr RAM zu geben, den die Node nicht hat, verschiebt den
OOM-Kill nur auf einen anderen Server — und dann faellt etwas aus, das vorher
lief.

Ist Luft da, schlage eine konkrete Erhoehung vor und nenne den gemessenen
Verbrauch als Begruendung. Ist keine Luft da, sag das deutlich: die Loesung ist
dann weniger Last oder mehr Hardware, nicht eine andere Zahl in der
Konfiguration.

## Merk dir, was du gelernt hast

Wenn du fuer ein bestimmtes Blueprint oder Modpack einen belastbaren Wert
gemessen hast, ist das eine Eigenschaft der Anlage und keine Vorliebe einer
Person — also Teamwissen. Halte es fest, damit der naechste Fall nicht wieder
bei null anfaengt.
