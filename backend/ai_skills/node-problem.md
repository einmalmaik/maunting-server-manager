---
name: Node hängt, ist offline oder überlastet
description: Mehrere Server auf demselben Host verhalten sich auffällig, eine Node meldet sich nicht mehr, Aktionen laufen ins Leere oder alles wird gleichzeitig langsam. Nutzen bei "bei einer meiner Nodes ist gerade ein Problem" und bei Störungen, die mehr als einen Server betreffen. Nicht nutzen, wenn es um einen einzelnen Server oder um dessen Einstellungen geht.
---

# Node hängt, ist offline oder überlastet

Wenn mehrere Server gleichzeitig zicken, liegt es fast nie an den Servern.
Prüfe den Host, bevor du dich in einen einzelnen Container vertiefst.

## 1. Gesamtbild holen

`read_node_health`. Sieh dir vier Dinge an, in dieser Reihenfolge:

1. **Letzter Kontakt.** Liegt er mehr als ein paar Minuten zurück, ist die
   Node praktisch offline. Alles Weitere ist dann Spekulation über veraltete
   Zahlen — sag das auch so.
2. **Docker verbunden.** Ist die Node erreichbar, aber Docker nicht verbunden,
   läuft der Agent, der Container-Dienst aber nicht. Das ist ein anderer
   Fehler als eine tote Node und braucht einen anderen Handgriff.
3. **Festplatte.** Der am häufigsten übersehene Punkt. Eine volle Platte
   äußert sich als scheinbar zufällige Startfehler, abgebrochene Backups und
   Datenbanken, die nicht mehr schreiben. Ab etwa 90 Prozent Belegung ist das
   die wahrscheinlichste Ursache für fast jede Störung auf dieser Node.
4. **CPU und RAM.** Dauerhaft nahe der Grenze heißt: die Node ist zu voll.
   Kurzzeitige Spitzen sind normal, besonders beim Start mehrerer Server.

## 2. Trifft es wirklich alle?

`list_my_servers` und der Status der Server auf dieser Node. Sind nur einzelne
betroffen, ist es kein Nodeproblem — geh zurück zum einzelnen Server.

## 3. Was du sagst

Bei einer offline gemeldeten Node: nenne den letzten Kontakt und sag klar, dass
alle weiteren Zahlen von diesem Zeitpunkt stammen. Nichts ist irreführender
als eine CPU-Auslastung von 3 Prozent, die in Wahrheit zwei Stunden alt ist.

Bei einer vollen Platte: nenne den freien Rest in absoluten Zahlen, nicht nur
in Prozent. "Noch 4 GB frei" ist eine Handlungsaufforderung, "91 Prozent
belegt" klingt nach einer Statistik.

Was du **nicht** weißt und nicht erfinden darfst: Hostnamen, IP-Adressen und
Standort der Node. `read_node_health` gibt sie bewusst nicht heraus — das
Modell soll Kapazität und Gesundheit vergleichen, nicht die Netzstruktur des
Betreibers kennen.
