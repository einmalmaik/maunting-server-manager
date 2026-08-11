---
name: Portkonflikt zwischen zwei Servern
description: Ein Server startet nicht wegen "address already in use", zwei Server streiten sich um denselben Port, oder nach dem Anlegen eines neuen Servers geht ein alter nicht mehr an. Nutzen bei Bindefehlern und Fragen zur Portvergabe. Nicht nutzen für Fragen zu Spielinhalten oder zu Werten in einer Konfigurationsdatei.
---

# Portkonflikt

Ein Port kann nur einmal belegt werden — pro Adresse. Der zweite Teil des
Satzes ist der, den man vergisst: zwei Server dürfen denselben Port benutzen,
wenn sie an *verschiedene* Adressen gebunden sind.

## 1. Wer belegt was?

`read_server_ports` für den betroffenen Server, dann `list_my_servers` und die
Ports der anderen. Achte auf die **Rolle** jedes Ports, nicht nur die Nummer:
Spielport, Abfrageport und RCON haben unterschiedliche Aufgaben, und der
Konflikt betrifft oft nur einen davon.

Vergiss das Protokoll nicht. TCP 27015 und UDP 27015 sind zwei verschiedene
Dinge und stehen sich nicht im Weg.

## 2. Ist es überhaupt ein anderer MSM-Server?

`check_server_reachability` sagt, ob der Port belegt ist — nicht, von wem.
Findest du unter den MSM-Servern keinen Kandidaten, belegt ihn etwas anderes
auf dem Host: ein Dienst des Betriebssystems, ein manuell gestarteter
Container, ein anderes Panel. Sag das dann so, statt weiter unter den
MSM-Servern zu suchen.

## 3. Was du vorschlägst

Bevorzugt: **den neuen Server verschieben**, nicht den laufenden. Der laufende
hat Spieler, gespeicherte Verbindungen und womöglich Einträge in
Serverlisten; der neue hat noch nichts davon.

Portänderungen laufen über den Netzwerk-Tab des Servers. Dort hängen sie an
Blueprint-Rollen und der Kollisionsprüfung, die ein Vorschlag von dir nicht
mitbringt — nenne dem Benutzer die konkrete freie Nummer und den Weg dorthin,
statt eine Änderung vorzuschlagen, die an einer Rolle scheitert.

Bei Spielen mit fest erwarteter Portnummer (Steam-Abfrageports etwa) ist das
Verschieben nicht immer folgenlos: manche Serverlisten finden den Server dann
nicht mehr. Sag das dazu, wenn es zutrifft.

## 4. Die Bind-IP als Ausweg

Sollen wirklich zwei Server denselben Port haben — etwa je einer pro
Netzwerkkarte — geht das über unterschiedliche Bind-IPs. `read_server_network`
zeigt die verfügbaren Adressen. Das ist die saubere Lösung für Betreiber mit
mehreren öffentlichen Adressen und die falsche für alle anderen, weil sie die
Erreichbarkeit einschränkt.
