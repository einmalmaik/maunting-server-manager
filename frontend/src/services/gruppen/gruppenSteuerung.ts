/**
 * Steuerumschlaege einer Gruppe: Schluessel annehmen, Anfragen beantworten.
 *
 * Alles hier laeuft im Lesepfad. Eine gescheiterte Zustellung darf den Verlauf
 * nicht abreissen, also wird jeder Fehler zu einem "noch nicht".
 */

import { base64ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'

import { eigenesGeraet, kontoNutztSignaturen } from '../e2eeGeraet'
import { abonniereMailbox } from '../mailboxAbo'
import { merkeMailboxNachweis } from '../mailboxNachweis'
import { pruefeNutzlast } from '../nutzlastSignatur'
import { istSchluesselhalter } from '../raumSchluessel'
import { ablage } from './gruppenAblage'
import { mailboxAusGeheimnis, merkeUmzug, nachweisAusGeheimnis } from './gruppenMailbox'
import { anJedesGeraet, frischeMitglieder, schluesselNutzlast } from './gruppenVerteilung'
import { fragenMitPraefix, kennstFrage, merkeFrage, vergissFrage } from './zustand'
import {
  GRUPPEN_ANFRAGE_TYP,
  GRUPPEN_SCHLUESSEL_TYP,
  KEY_ID_LAENGE,
  KEY_ID_MUSTER,
  SCHLUESSEL_BYTES,
  gleicheMitglieder,
  normalisiereMitglieder,
  type GruppenKontext,
  type GruppenSchluesselEintrag,
  type GruppenSteuerung,
} from './typen'

/**
 * Nimmt einen entschlüsselten Hybrid-Klartext aus einer Gruppenmailbox entgegen.
 *
 * Meldet `keine`, wenn es keiner der beiden Steuertypen war — dann gehört der
 * Klartext in den normalen Lesepfad.
 */
export async function verarbeiteGruppenSteuerung(
  kontext: GruppenKontext,
  klartext: string,
): Promise<GruppenSteuerung> {
  let roh: Record<string, unknown>
  try {
    const geparst = JSON.parse(klartext)
    if (!geparst || typeof geparst !== 'object') return { art: 'keine' }
    roh = geparst as Record<string, unknown>
  } catch {
    return { art: 'keine' }
  }

  // Ein Umschlag aus einer fremden Gruppe darf hier nichts setzen. Ohne diese
  // Prüfung könnte ein Mitglied von A einen Schlüssel für B unterschieben.
  if (Number(roh.groupId) !== kontext.groupId) return { art: 'keine' }

  if (roh.typ === GRUPPEN_SCHLUESSEL_TYP) {
    return nimmSchluessel(kontext, roh)
  }
  if (roh.typ === GRUPPEN_ANFRAGE_TYP) {
    return beantworteAnfrage(kontext, roh)
  }
  return { art: 'keine' }
}

async function nimmSchluessel(
  kontext: GruppenKontext,
  roh: Record<string, unknown>,
): Promise<GruppenSteuerung> {
  const pruefung = await pruefeNutzlast(kontext.blindMailboxId, roh)
  if (pruefung.art === 'gefaelscht') {
    console.warn('[E2EE] Gruppenschlüssel mit ungültiger Signatur abgewiesen', {
      groupId: kontext.groupId,
      behauptet: pruefung.behauptet,
    })
    return { art: 'keine' }
  }

  const gueltigeMitglieder = new Set([...kontext.mitglieder, kontext.eigeneId])

  if (pruefung.art === 'geprueft') {
    if (!gueltigeMitglieder.has(pruefung.vonKonto)) {
      console.warn('[E2EE] Gruppenschlüssel von Nicht-Mitglied abgewiesen', {
        groupId: kontext.groupId,
        vonKonto: pruefung.vonKonto,
      })
      return { art: 'keine' }
    }
  } else {
    // unsigniert
    const behauptet = Number(roh.von_konto ?? roh.absenderId ?? 0)
    if (behauptet > 0) {
      if (!gueltigeMitglieder.has(behauptet)) {
        console.warn('[E2EE] Unsignierter Gruppenschlüssel von Nicht-Mitglied abgewiesen', {
          groupId: kontext.groupId,
          vonKonto: behauptet,
        })
        return { art: 'keine' }
      }
      if (await kontoNutztSignaturen(behauptet)) {
        console.warn('[E2EE] Unsignierter Gruppenschlüssel trotz Signaturfähigkeit abgewiesen', {
          groupId: kontext.groupId,
          vonKonto: behauptet,
        })
        return { art: 'keine' }
      }
    }
  }

  const keyId = String(roh.keyId ?? '')
  const schluessel = String(roh.schluessel ?? '')
  if (!KEY_ID_MUSTER.test(keyId) || !schluessel) return { art: 'keine' }

  let bytes: Uint8Array
  try {
    bytes = base64ToBytes(schluessel)
  } catch {
    return { art: 'keine' }
  }
  try {
    if (bytes.length !== SCHLUESSEL_BYTES) return { art: 'keine' }
    // Die Kennung wird nachgerechnet. Sie ist der Hash des Schlüssels, also
    // lässt sich unter einer bekannten Kennung kein anderer Schlüssel
    // unterschieben — und ein Gerät, das denselben Schlüssel zweimal bekommt,
    // schreibt zweimal dasselbe.
    const gerechnet = (await sha256Hex(bytes)).slice(0, KEY_ID_LAENGE)
    if (gerechnet !== keyId) return { art: 'keine' }
  } finally {
    bytes.fill(0)
  }

  // Die mitgeschickte Mitgliederliste entscheidet nur, wann *dieses* Gerät das
  // nächste Mal rotiert. Eine falsche Angabe verzögert die Rotation bis zum
  // nächsten eigenen Sendevorgang — mehr kann sie nicht, denn wer den Schlüssel
  // münzt, hat ihn ohnehin.
  const mitglieder = normalisiereMitglieder(Array.isArray(roh.mitglieder) ? roh.mitglieder : [])
  const eintrag: GruppenSchluesselEintrag = {
    groupId: kontext.groupId,
    keyId,
    schluessel,
    mitglieder,
    erzeugtAm: new Date().toISOString(),
  }

  /*
   * Das Gruppengeheimnis reist mit. Wer zuerst da war, bleibt stehen — das
   * entscheidet die Ablage, nicht diese Stelle.
   *
   * Dass ein Mitglied hier ein falsches Geheimnis unterschieben könnte, ist
   * dieselbe Lage wie beim Schlüssel selbst: wer eines schickt, hat den
   * Schlüssel ohnehin. Wirkung hätte es nur bei einem Gerät, das noch gar
   * keines kennt, und die Folge wäre kein Mitlesen, sondern ein Gerät, das in
   * der falschen Mailbox sucht und nichts mehr sieht. Kryptographisch
   * abgesichert wird die Herkunft erst mit MLS; bis dahin ist die
   * Erstankunft die Regel.
   */
  const geheimnis = typeof roh.geheimnis === 'string' ? roh.geheimnis : ''
  if (geheimnis) {
    try {
      await ablage.schreibeGeheimnis({
        groupId: kontext.groupId,
        geheimnis,
        erzeugtAm: new Date().toISOString(),
      })
      // Auf der Kennung aus dem Geheimnis, nicht auf der alten: ab hier liest
      // und sendet dieses Gerät dort. Die alte bleibt daneben im Abo, damit
      // der Bestand und die noch nicht umgezogenen Mitglieder nicht wegfallen.
      const neu = await mailboxAusGeheimnis(geheimnis)
      const token = await nachweisAusGeheimnis(geheimnis)
      merkeMailboxNachweis(neu, token)
      abonniereMailbox(neu, token)
      merkeUmzug(kontext.blindMailboxId, neu)
      abonniereMailbox(kontext.blindMailboxId)
    } catch {
      // Ein Geheimnis, das sich nicht ablegen lässt, darf den Schlüssel nicht
      // aufhalten: ohne den wäre die Nachricht unlesbar, ohne jenes nur die
      // Mailbox ungeschützt.
    }
  }

  /*
   * Ankommen ja, verdrängen nein.
   *
   * Ein zugestellter Schlüssel wird immer abgelegt — ohne ihn wäre die
   * Nachricht unlesbar, für die er gilt. Der *aktuelle* wird er aber nur, wenn
   * für die heutige Mitgliedschaft noch keiner da ist.
   *
   * Bis 09/2026 gewann der zuletzt eingetroffene. Damit konnte ein Mitglied
   * einem anderen einen nur ihm bekannten Schlüssel unterschieben: das Opfer
   * verschlüsselte ab da gegen ihn, und der Rest der Gruppe las nur noch
   * „Verschlüsselte Nachricht". Wer zuerst da ist, bleibt jetzt stehen — und
   * das ist genau der, der beim letzten Mitgliederwechsel als erster gesendet
   * hat, also der vorgesehene Weg.
   *
   * Verglichen wird gegen `kontext.mitglieder`, die Momentaufnahme der
   * Oberfläche — **nicht** gegen `frischeMitglieder`. Zwei Gründe: die Liste
   * aus der Zustellung ist die des Absenders und damit Teil des Angriffs, und
   * `frischeMitglieder` fragt den Server. Diese Funktion läuft bei *jedem*
   * Abruf über *jeden* Schlüsselumschlag, der in der Mailbox liegt — also alle
   * fünf Sekunden. Ein Netzaufruf an dieser Stelle wäre eine Dauerlast.
   *
   * Was die Momentaufnahme kostet, wenn sie veraltet ist: dieses Gerät behält
   * seinen alten Schlüssel, münzt beim nächsten Senden einen frischen (dort
   * entscheidet `frischeMitglieder`) und verteilt ihn. Die Gruppe hat dann
   * eine Generation mehr als nötig. Lesbar bleibt alles.
   *
   * Was das nicht kann: ein Gerät, das noch gar keinen Schlüssel für diese
   * Mitgliedschaft hat, nimmt weiterhin den ersten, der kommt. Ohne eine
   * Absenderbeglaubigung auf der Zustellung ist das nicht enger zu fassen; die
   * Grenze steht so auch in `docs/agent-rules/security.md`.
   */
  const vorhanden = await ablage.liesAktuellen(kontext.groupId)
  const behalte =
    vorhanden !== null &&
    vorhanden.keyId !== keyId &&
    gleicheMitglieder(
      vorhanden.mitglieder,
      normalisiereMitglieder([...kontext.mitglieder, kontext.eigeneId]),
    )

  if (behalte) {
    await ablage.schreibeNebenher(eintrag)
    return { art: 'schluessel', keyId }
  }

  await ablage.schreibe(eintrag)
  return { art: 'schluessel', keyId }
}

/**
 * Höchstens drei Schlüsselantworten je Mitglied und Gruppe in zehn Minuten.
 *
 * Gezählt wird je Konto, nicht je Gerät: die Gerätekennung steht in der
 * Anfrage, und wer sie frei erfindet, hätte sonst für jede erfundene Kennung
 * drei neue Antworten. So erschöpft ein Mitglied mit einer Flut von Anfragen
 * nicht das Relais-Kontingent des Antwortenden (HTTP 429 beim eigenen Senden).
 * Drei reichen für ein Konto, das mehrere Geräte zugleich neu einrichtet.
 */
export const MAX_ANTWORTEN_FENSTER_MS = 10 * 60_000
export const MAX_ANTWORTEN_PRO_FENSTER = 3

const antwortZeiten = new Map<string, number[]>()

export function leereAntwortSperren(): void {
  antwortZeiten.clear()
}

/**
 * Wer einem Nachzügler antwortet, entscheidet jedes Gerät für sich aus der
 * Mitgliederliste — dieselbe Regel wie beim Raumschlüssel eines Anrufs.
 *
 * Dazu kommt ein Fall, den ein Anruf nicht kennt: fragt ein **zweites Gerät
 * desselben Kontos**, antworten die eigenen Geräte immer. `istSchluesselhalter`
 * schließt das fragende Konto aus den Kandidaten aus, und ein frisch gekoppeltes
 * Gerät in einer Gruppe, deren übrige Mitglieder gerade offline sind, bekäme
 * sonst nie einen Schlüssel.
 */
async function beantworteAnfrage(
  kontext: GruppenKontext,
  roh: Record<string, unknown>,
): Promise<GruppenSteuerung> {
  const anfragerId = Number(roh.vonKonto)
  if (!Number.isInteger(anfragerId) || anfragerId <= 0) return { art: 'keine' }
  // Nur Mitglieder bekommen Schlüssel. Und die Geräteschlüssel kommen aus dem
  // Verzeichnis, nie aus der Anfrage — sonst bestimmte der Fragende selbst,
  // wogegen versiegelt wird.
  //
  // Die Liste kommt vom Server und nicht aus `kontext.mitglieder`: wer gerade
  // erst beigetreten ist, steht in der Momentaufnahme der Oberfläche noch
  // nicht, und genau der fragt hier. Mit der alten Liste wurde er abgewiesen,
  // ohne dass irgendwo etwas davon stand.
  const mitglieder = await frischeMitglieder(kontext)
  if (!mitglieder.includes(anfragerId)) return { art: 'keine' }

  const eigenesKonto = anfragerId === kontext.eigeneId
  const zustaendig =
    eigenesKonto || istSchluesselhalter(kontext.eigeneId, mitglieder, anfragerId)
  if (!zustaendig) return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }

  // 1. Dedup je anfragendem Gerät und keyId (verhindert Mehrfachantworten auf denselben Umschlag in der Mailbox)
  const keyIdStr = String(roh.keyId ?? '')
  const geraeteKennung = `${anfragerId}:${String(roh.vonGeraet ?? '')}:${keyIdStr}`
  if (await ablage.kennstAnfrage(kontext.groupId, geraeteKennung)) {
    return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }
  }

  const eintrag = await ablage.liesAktuellen(kontext.groupId)
  if (!eintrag) return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }

  // Und nur, wem dieser Schlüssel gemünzt wurde.
  if (!eintrag.mitglieder.includes(anfragerId)) {
    return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }
  }

  // 2. Flutschutz: Rate-Limiting gegen Fluten mit erfundenen keyIds oder Geräten
  // Höchstens drei Antworten je Konto und Gruppe im Fenster
  const sperrKey = `${kontext.groupId}:${anfragerId}`
  const jetzt = Date.now()
  const zeiten = (antwortZeiten.get(sperrKey) ?? []).filter(
    (t) => jetzt - t < MAX_ANTWORTEN_FENSTER_MS,
  )
  if (zeiten.length >= MAX_ANTWORTEN_PRO_FENSTER) {
    return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }
  }

  // Nur der aktuelle Schlüssel. Ältere bleiben hier, sonst holte sich ein
  // Zurückgekehrter über eine Anfrage den ganzen Verlauf.
  //
  // Hier darf nichts fliegen: das läuft mitten im Lesen der Mailbox, und eine
  // gescheiterte Antwort darf nicht den Verlauf des Antwortenden abreissen. Der
  // Fragende versucht es in einer Minute erneut.
  let zugestellt = 0
  try {
    const nutzlast = await schluesselNutzlast(
      kontext,
      eintrag,
      (await ablage.liesGeheimnis(kontext.groupId))?.geheimnis ?? null,
    )
    zugestellt = (
      await anJedesGeraet(
        kontext,
        [anfragerId],
        nutzlast,
        GRUPPEN_SCHLUESSEL_TYP,
      )
    ).anzahl
  } catch {
    zugestellt = 0
  }
  if (zugestellt > 0) {
    zeiten.push(jetzt)
    antwortZeiten.set(sperrKey, zeiten)
    await ablage.merkeAnfrage(kontext.groupId, geraeteKennung).catch(() => {})
  }
  return { art: 'anfrage', vonKonto: anfragerId, beantwortet: zugestellt > 0 }
}

export async function fordereGruppenSchluessel(
  kontext: GruppenKontext,
  fehlendeKeyId: string,
): Promise<boolean> {
  const marke = `${kontext.groupId}:${fehlendeKeyId}`
  if (kennstFrage(marke)) return false
  // Sofort merken, damit zwei überlappende Durchläufe nicht beide fragen.
  merkeFrage(marke)

  try {
    // Auch hier die Liste vom Server: der Fragende ist oft gerade erst
    // beigetreten, und wen er fragt, muss zur heutigen Gruppe passen.
    const mitglieder = await frischeMitglieder(kontext)
    const andere = mitglieder.filter((id) => id !== kontext.eigeneId)
    const ziele = andere.length > 0 ? [Math.min(...andere), kontext.eigeneId] : [kontext.eigeneId]

    const meins = await eigenesGeraet()
    const nutzlast = JSON.stringify({
      typ: GRUPPEN_ANFRAGE_TYP,
      v: 1,
      groupId: kontext.groupId,
      vonKonto: kontext.eigeneId,
      vonGeraet: meins.kennung,
      keyId: fehlendeKeyId,
    })

    const erreicht = (await anJedesGeraet(kontext, ziele, nutzlast, GRUPPEN_ANFRAGE_TYP)).anzahl
    // Nichts rausgegangen heißt: es liegt auch nichts in der Mailbox, das
    // jemand später noch beantworten könnte. Also Marke zurück.
    if (erreicht === 0) vergissFrage(marke)
    return erreicht > 0
  } catch {
    // Auch das läuft im Lesepfad: eine Nachfrage, die niemanden erreicht, ist
    // ein „noch nicht", kein Grund, die Anzeige abzubrechen. Ein Netzfehler
    // darf dieses Gerät aber nicht bis zum Neuladen ohne Schlüssel lassen.
    vergissFrage(marke)
    return false
  }
}

/** Wirft die Schlüssel einer Gruppe weg. Beim Verlassen und beim Löschen fällig. */
export async function verwirfGruppenSchluessel(groupId: number): Promise<void> {
  for (const marke of fragenMitPraefix(`${groupId}:`)) {
    if (marke.startsWith(`${groupId}:`)) vergissFrage(marke)
  }
  await ablage.loescheGruppe(groupId)
}
