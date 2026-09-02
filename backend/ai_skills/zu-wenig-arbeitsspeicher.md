---
name: Zu wenig Arbeitsspeicher, OOM-Kill erkennen
description: Ein Server wird ohne Fehlermeldung beendet, stürzt unter Last ab oder läuft nach einiger Zeit immer langsamer. Nutzen bei Exit code 137, "Killed", wiederkehrenden Abstürzen zur Stoßzeit und Fragen nach der richtigen RAM-Größe. Nicht nutzen, wenn der Server sauber und mit einer Fehlermeldung endet.
---

# Zu wenig Arbeitsspeicher

Der OOM-Kill ist der täuschendste Fehler im Serverbetrieb: es gibt keine
Fehlermeldung. Der Kernel beendet den Prozess, und das Log hört einfach auf.
Wer danach im Log nach einer Ursache sucht, findet keine — und verdächtigt
dann Mods, Netzwerk oder die Spielsoftware.

## Woran du ihn erkennst

Drei Anzeichen, zusammen praktisch beweisend:

1. `read_server_logs` endet **mitten im Betrieb**, ohne Abschiedszeile. Kein
   "Shutting down", kein Stacktrace, kein Fehler.
2. `read_server_status` meldet `Exit code 137`. Das ist 128 + 9, also SIGKILL —
   der Prozess wurde beendet, nicht gebeten.
3. `read_server_capacity` zeigt eine RAM-Grenze nahe am tatsächlichen
   Verbrauch kurz vor dem Ende.

Fehlt Punkt 2, reichen 1 und 3 für eine begründete Vermutung — sag dann auch
"vermutlich", nicht "sicher".

## Wieviel Speicher wirklich nötig ist

Faustwerte für die häufigen Spiele. Sie sind Startpunkte, keine Wahrheiten —
Mods und Spielerzahl verschieben alles nach oben:

| Spiel | Ohne Mods | Mit Mods |
|---|---|---|
| Minecraft (Vanilla) | 2–4 GB | 6–8 GB, modpackabhängig deutlich mehr |
| Valheim | 4 GB | 6–8 GB |
| Rust | 8 GB | 12 GB und mehr, stark kartengrößenabhängig |
| ARK | 8 GB | 12–16 GB |
| DayZ | 4 GB | 6–8 GB |
| Palworld | 8 GB | 16 GB |

Rechne bei Minecraft-Modpacks nicht mit dem Wert des Packs, sondern mit dem
Wert plus etwa ein Drittel: die Angaben der Packersteller gelten für den
Einzelspielerbetrieb.

## Was du vorschlägst

Prüfe zuerst mit `read_node_health`, ob auf dem Host überhaupt Luft ist.
Einem Server mehr RAM zu geben, den die Node nicht hat, verschiebt den
OOM-Kill nur auf einen anderen Server — und dann fällt etwas aus, das vorher
lief.

Ist Luft da, schlage eine konkrete Erhöhung vor und nenne den gemessenen
Verbrauch als Begründung. Ist keine Luft da, sag das deutlich: die Lösung ist
dann weniger Last oder mehr Hardware, nicht eine andere Zahl in der
Konfiguration.

## Merk dir, was du gelernt hast

Wenn du für ein bestimmtes Blueprint oder Modpack einen belastbaren Wert
gemessen hast, ist das eine Eigenschaft der Anlage und keine Vorliebe einer
Person — also Teamwissen. Halte es fest, damit der nächste Fall nicht wieder
bei null anfängt.
