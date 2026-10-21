/**
 * Der Gruppenschlüssel: erzeugen, zustellen, rotieren, nachfordern.
 *
 * Eine Gruppe kann keinen Double Ratchet fahren. Der Ratchet ist ein Faden
 * zwischen genau zwei Geräten; bei zwanzig Mitgliedern mit je drei Geräten wären
 * das je Nachricht sechzig Umschläge. Signal löst das mit Sender Keys, und genau
 * das steht hier: ein Zufallsschlüssel je Gruppe, verschlüsselt wird einmal,
 * zugestellt wird nur der **Schlüssel** — einzeln versiegelt gegen jedes Gerät
 * jedes Mitglieds.
 *
 * **Warum nicht abgeleitet.** Vorher war der Gruppenschlüssel
 * `sha256("msm:group:key:" + groupId)`. Die `groupId` steht in der Datenbank,
 * also konnte das Backend jede Gruppennachricht mitlesen. Das war keine
 * Ende-zu-Ende-Verschlüsselung, sondern eine, die genau den nicht aussperrt, den
 * sie aussperren soll. Dieser Absatz bleibt stehen, damit niemand sie neu
 * erfindet.
 *
 * **Die Kennung ist der Schlüssel.** `keyId = sha256Hex(schluessel).slice(0,16)`.
 * Das ist nicht bloß eine Nummer: wer einen Schlüssel zustellt, kann ihn nicht
 * unter der Kennung eines anderen unterschieben — beim Empfang wird die Kennung
 * nachgerechnet. Eine Nachricht nennt die Kennung im Klartext, damit jedes Gerät
 * weiß, welchen seiner Schlüssel es nehmen muss.
 *
 * **Zustellung über die Gruppenmailbox.** Die Umschläge sind gewöhnliche
 * `sv-e2ee-hybrid-v1:`, relayed als Steuerumschläge in dieselbe Mailbox wie die
 * Nachrichten. Das Backend braucht dafür nichts Neues, und es entstehen keine
 * Direktchat-Zeilen zwischen Mitgliedern, die einander gar nicht kennen. Jeder
 * lädt alle Umschläge, öffnen lässt sich nur der eigene.
 *
 * **Rotation.** Jeder Schlüssel merkt sich, für welche Mitgliedschaft er
 * gemünzt wurde. Vor dem Senden wird das mit der heutigen Mitgliederliste
 * verglichen; weicht sie ab, entsteht ein frischer Schlüssel. Wer die Gruppe
 * verlassen hat, liest damit nichts Neues mehr — die Nachrichten von vorher
 * bleiben ihm lesbar, denn seinen alten Schlüssel kann ihm niemand wegnehmen.
 *
 * Die Prüfung läuft beim Absender und nicht an einem zentralen Ereignis, weil
 * ein Ereignis jemanden braucht, der es mitbekommt: wer rauswirft und danach den
 * Tab schließt, hätte die Rotation nie ausgelöst. So entscheidet jedes Gerät für
 * sich, aus derselben Mitgliederliste, und es heilt sich von selbst.
 *
 * **Nachzügler.** Ein Gerät ohne passenden Schlüssel fragt nach. Beantwortet
 * wird nur der **aktuelle** Schlüssel, nie ein alter: sonst könnte sich jemand
 * hinauswerfen lassen, über einen offenen Einladungslink zurückkommen und den
 * gesamten Verlauf nachfordern.
 *
 * Eine Nachfrage wird **einmal** beantwortet. Sie ist ein Umschlag und bleibt
 * als solcher in der Mailbox liegen, und der Lesepfad holt bei jedem Abruf das
 * ganze Fenster neu — ohne Gedächtnis antwortete der Zuständige deshalb alle
 * fünf Sekunden erneut, mit einem Umschlag je Gerät des Fragenden. Am laufenden
 * System waren danach 99 von 100 Umschlägen der Gruppenmailbox
 * Schlüsselzustellungen: die Nachrichten fielen aus dem Fenster, und im Verlauf
 * stand nichts mehr ausser „Verschlüsselte Nachricht". Gemerkt wird je
 * fragendem Gerät und fehlender Schlüsselkennung — fragt dasselbe Gerät später
 * nach einer anderen Kennung, bekommt es wieder eine Antwort.
 *
 * **Eine Ablage je Konto, seit 09/2026.** Sie hiess schlicht
 * `msm_e2ee_gruppen` und war damit die einzige der vier E2EE-Ablagen ohne
 * Bindung an einen Menschen: der Geräteschlüssel liegt unter `konto:<id>`, der

Die Umsetzung liegt in `services/gruppen/`: Ablage (`gruppenAblage`), Geheimnis
und Mailbox (`gruppenMailbox`), Erzeugen und Zustellen (`gruppenVerteilung`),
Ver- und Entschluesseln (`gruppenNachricht`), Steuerumschlaege
(`gruppenSteuerung`), Direktchat-Geheimnis (`dmGeheimnis`), die eigene
Geraete-Mailbox (`geraeteSteuerung`) und das Mailbox-Abo (`gespraechsAbo`).
Diese Datei bleibt die oeffentliche Fassade, damit kein Aufrufer einen neuen
Pfad lernen muss.
 */

export {
  GRUPPE_PREFIX,
  GRUPPEN_ANFRAGE_TYP,
  GRUPPEN_SCHLUESSEL_TYP,
  GruppenSchluesselNichtZugestelltError,
  type DmGeheimnis,
  type GruppenAblage,
  type GruppenGeheimnis,
  type GruppenKontext,
  type GruppenLesung,
  type GruppenSchluesselEintrag,
  type GruppenSteuerung,
} from './gruppen/typen'

export {
  schreibeGruppenBestandNeu,
  setzeGruppenAblageFuerTest,
} from './gruppen/gruppenAblage'

export {
  type GruppenZiele,
  gruppenGeheimnis,
  gruppenZiele,
  leereUmzuege,
  mailboxAusGeheimnis,
  nachweisAusGeheimnis,
  umgezogeneMailbox,
} from './gruppen/gruppenMailbox'

export { abonniereBekannteGespraeche } from './gruppen/gespraechsAbo'

export { dmZiele, fordereDmGeheimnis } from './gruppen/dmGeheimnis'

export { erzeugeGruppenSchluessel } from './gruppen/gruppenVerteilung'

export {
  entschluesseleGruppenUmschlag,
  verschluesseleFuerGruppe,
} from './gruppen/gruppenNachricht'

export {
  MAX_ANTWORTEN_FENSTER_MS,
  MAX_ANTWORTEN_PRO_FENSTER,
  fordereGruppenSchluessel,
  leereAntwortSperren,
  verarbeiteGruppenSteuerung,
  verwirfGruppenSchluessel,
} from './gruppen/gruppenSteuerung'

export {
  holeGeraeteSteuerung,
  leereGeraeteStand,
} from './gruppen/geraeteSteuerung'
