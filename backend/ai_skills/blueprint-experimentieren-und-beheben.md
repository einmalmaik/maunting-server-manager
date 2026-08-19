---
name: Startparameter im Blueprint ändern
description: Ein Startbefehl, eine Umgebungsvariable oder ein Image im Blueprint stimmt nicht — der Server startet mit falschen Parametern, taucht nicht im Serverbrowser auf oder ignoriert Vorgaben. Nutzen beim Ableiten, Ändern und Aufräumen von Blueprints und beim Umstellen eines Servers auf einen anderen Blueprint. Nicht nutzen für Werte in Konfigurationsdateien des Servers oder für reine Statusfragen.
---

# Startparameter im Blueprint ändern

Zwei Sätze, aus denen sich alles Weitere ergibt: Ein Blueprint gilt für **alle**
Server seines Typs. Und der Wechsel eines Servers auf einen anderen Blueprint
ist keine Umschaltung, sondern eine Neuinstallation. Wer daraus eine Schleife
aus "umstellen und ausprobieren" macht, leert den Server des Benutzers — jedes
Mal aufs Neue.

## 1. Erst begründen, dann ändern

`read_blueprint` zeigt Startbefehl (`runtime.startup`), Umgebung (`runtime.env`),
Image und Ports.

Jede Änderung braucht eine Quelle: eine Logzeile aus `read_server_logs`, die
offizielle Dokumentation des Spiels (`web_search`) oder die
MSM-Dokumentation. Ein Parameter, den du
"mal probierst", beweist nichts — du weißt hinterher weder, ob er geholfen hat,
noch warum.

## 2. Abgeleiteten Blueprint anlegen

Mitgelieferte Blueprints (`origin: native`) sind schreibgeschützt.
`propose_blueprint_change` legt daraus einen eigenen Community-Blueprint mit
deinen Änderungen an; die Vorlage bleibt unberührt.

Dieser Schritt allein ändert an **keinem** Server etwas. Melde danach keinen
Erfolg, sondern kündige den zweiten Schritt an.

## 3. Der Wechsel — und was er kostet

`propose_server_blueprint_switch` stellt einen Server auf den neuen Blueprint um.
Zwei Dinge musst du vorher sagen und tun:

- **Der Server ist vorher zu stoppen.** Ein Vorschlag für einen laufenden Server
  wird abgewiesen. Stopp ihn also vorher mit `propose_server_lifecycle`
  (`operation: "stop"`) — das ist ein eigener Vorschlag, kein Nebeneffekt.
- **Der Wechsel löscht das gesamte Serververzeichnis.** Er legt ein
  Pflicht-Backup an, wirft Welten, Konfigurationen und Mods weg, vergibt die
  Ports neu und installiert das Spiel frisch. Sag dem Benutzer genau das,
  bevor du den Vorschlag machst — nicht "ich stelle kurz um".

Willst du wirklich ausprobieren, dann an einem eigens dafür angelegten Server
(`propose_server_create`), nie am Server, um den es dem Benutzer geht. Sag ihm
vorher, dass und wozu du einen anlegst.

## 4. Aufräumen ist nicht selbstverständlich

Sei hier ehrlich, statt "ich räume auf" zu melden:

- Den Testserver entfernt `propose_server_delete`, und der läuft **nie** ohne
  Bestätigung des Benutzers, auch bei erteilter Freigabe. Kündige ihn an, statt
  Vollzug zu melden.
- Solange dieser Server steht, weist `propose_blueprint_delete` den
  Test-Blueprint ab: ein Blueprint, den noch ein Server benutzt, wird nicht
  gelöscht. Die Reihenfolge ist also erst Server, dann Blueprint.
- Mitgelieferte Blueprints lassen sich nicht löschen.

Sag am Ende, was übrig geblieben ist und was der Benutzer dafür noch bestätigen
muss. Ein vergessener Testserver kostet RAM und Ports.
