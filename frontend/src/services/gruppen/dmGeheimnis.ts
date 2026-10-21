/**
 * Das Chatgeheimnis eines Direktchats: erzeugen, verteilen, annehmen.
 *
 * Erzeugen darf nur die kleinere Kennung. Beide Seiten kommen ohne Rueckfrage
 * zum selben Ergebnis - sonst haette ein Gespraech zwei Mailboxen.
 */

import { randomBytes } from '@msdis/shield/random'
import { bytesToBase64 } from '@msdis/shield/core'

import { registriereMailbox } from '@/api/social'
import { deriveBlindMailboxId } from '../e2eeCrypto'
import { eigenesGeraet, geraeteVon, kontoNutztSignaturen } from '../e2eeGeraet'
import { abonniereMailbox } from '../mailboxAbo'
import { mailboxNachweis, merkeMailboxNachweis } from '../mailboxNachweis'
import { pruefeNutzlast, signiereNutzlast } from '../nutzlastSignatur'
import { ablage } from './gruppenAblage'
import {
  beiDerAltenKennung,
  mailboxAusGeheimnis,
  merkeUmzug,
  nachweisAusGeheimnis,
  type GruppenZiele,
} from './gruppenMailbox'
import { anJedesGeraet } from './gruppenVerteilung'
import {
  SCHLUESSEL_BYTES,
  type DmGeheimnis,
  type GruppenKontext,
} from './typen'

/** Der Steuerumschlag, mit dem ein Chatgeheimnis zu den Geräten reist. */
export const DM_GEHEIMNIS_TYP = 'dm_secret'
/**
 * Ein eigenes Gerät fragt seine Geschwister nach dem Chatgeheimnis.
 *
 * Der Weg ist nötig, weil ein neu gekoppeltes Gerät das Geheimnis weder
 * erzeugen noch aus der Chat-Mailbox holen kann: es kennt die neue Mailbox
 * nicht und liest nur die alte. Ohne diese Nachfrage bliebe es für immer auf
 * der alten, während jedes andere Gerät ausschließlich in die neue schreibt.
 */
export const DM_ANFRAGE_TYP = 'dm_secret_request'

/**
 * Wer ein Chatgeheimnis erzeugen darf: das **kleinere Konto**.
 *
 * Bei der Gruppe ist es der Eigentümer. Ein Direktchat hat keinen, also
 * braucht es eine Regel, die beide Seiten unabhängig voneinander zum selben
 * Ergebnis führt — sonst erzeugen beide eines, das Gespräch hat zwei Mailboxen
 * und zerfällt in zwei Hälften, die einander nicht mehr sehen.
 *
 * Die kleinere Kennung ist willkürlich, aber eindeutig und ohne Rückfrage
 * bestimmbar. Wer nicht darf, wartet: das Geheimnis kommt mit der nächsten
 * Zustellung der Gegenseite.
 */
function darfDmGeheimnisErzeugen(eigeneId: number, peerId: number): boolean {
  return eigeneId < peerId
}

/**
 * Verteilt ein Chatgeheimnis an jedes Gerät beider Seiten.
 *
 * Über die Geräte-Mailboxen, nicht über die Chatmailbox — aus demselben Grund
 * wie bei der Gruppe: durch die neue Mailbox darf nichts kommen, was man zum
 * Öffnen der neuen Mailbox bräuchte. Sonst steht die Gegenseite vor einem
 * Schloss, dessen Schlüssel dahinter liegt.
 *
 * **Gezählt wird nur die Gegenstelle**, und darum laufen es zwei Aufrufe statt
 * einem. Die eigenen Zweitgeräte zu erreichen ist schön, sagt aber nichts
 * darüber, ob der Umzug tragfähig ist — und genau diese Zahl entscheidet
 * darüber (siehe `verteiltAm`). Eine Summe über beide Seiten wäre hier ein
 * stilles Ja auf eine Frage, die niemand gestellt hat.
 *
 * **Die eigenen Geräte werden namentlich zurückgegeben.** Bis 10/2026 war das
 * eine stille Nebenwirkung ohne Gedächtnis: ein Gerät, das erst nach der
 * ersten Zustellung dazukam, erfuhr das Geheimnis nie wieder — es las die neue
 * Mailbox und sah nichts. Mit den Kennungen in `verteiltAn` weiß der nächste
 * Versand, wem noch etwas fehlt (siehe `eigeneGeraeteOhne`).
 */
async function verteileDmGeheimnis(
  eigeneId: number,
  peerId: number,
  geheimnis: string,
  { nurEigene, beleg }: { nurEigene?: readonly string[]; beleg?: Record<string, unknown> } = {},
): Promise<{ gegenstelle: number; eigene: string[] }> {
  // Beglaubigt, und zwar gebunden an die **abgeleitete Gesprächskennung** und
  // nicht an die Geräte-Mailbox, in die der Umschlag fällt: das Paket geht an
  // viele Mailboxen, eine Unterschrift je Ziel wäre eine je Gerät. Die
  // abgeleitete Kennung nennt genau dieses Gespräch, beide Seiten können sie
  // ausrechnen, und dieselbe Unterschrift lässt sich damit in kein anderes
  // umhängen.
  // Ein weitergegebener Umschlag bleibt **der der Gegenseite**: nur er trägt
  // die Unterschrift der kleineren Kennung, und nur die nimmt
  // `nimmDmGeheimnis` an (siehe `DmGeheimnis.beleg`).
  const nutzlast =
    beleg ??
    (await signiereNutzlast(await deriveBlindMailboxId(eigeneId, peerId), eigeneId, {
      typ: DM_GEHEIMNIS_TYP,
      v: 1,
      peerId,
      eigeneId,
      geheimnis,
    }))
  const kontext: GruppenKontext = {
    groupId: 0,
    blindMailboxId: '',
    eigeneId,
    mitglieder: [],
    istEigentuemer: false,
  }
  const text = JSON.stringify(nutzlast)

  // Nachtrag an einzelne eigene Geräte: die Gegenstelle hat es längst, und ein
  // erneuter Umschlag an sie wäre nur Rauschen.
  if (nurEigene) {
    try {
      const { kennungen } = await anJedesGeraet(
        kontext,
        [eigeneId],
        text,
        DM_GEHEIMNIS_TYP,
        nurEigene,
      )
      return { gegenstelle: 0, eigene: kennungen }
    } catch {
      return { gegenstelle: 0, eigene: [] }
    }
  }

  // Zuerst die Gegenstelle — diese Zahl zählt.
  let gegenstelle = 0
  try {
    gegenstelle = (await anJedesGeraet(kontext, [peerId], text, DM_GEHEIMNIS_TYP)).anzahl
  } catch {
    // `anJedesGeraet` wirft, wenn es Ziele gab und keines erreicht wurde. Für
    // die Gruppe ist das ein Abbruch; hier ist es schlicht eine Null.
    gegenstelle = 0
  }

  // Dann die eigenen Zweitgeräte. Bestes Bemühen: erreicht es keines, schreibt
  // das Handy vorerst in die alte Mailbox — und die wird ohnehin mitgelesen.
  let eigene: string[] = []
  try {
    eigene = (await anJedesGeraet(kontext, [eigeneId], text, DM_GEHEIMNIS_TYP)).kennungen
  } catch {
    /* nachholbar */
  }
  return { gegenstelle, eigene }
}

/**
 * Wann dieses Gerät zuletzt nach einem Chatgeheimnis gefragt hat, je Peer.
 *
 * Die Nachfrage steht im Lesepfad und läuft im Fünf-Sekunden-Takt. Ohne
 * Drosselung schickte ein Gerät, das noch keines hat, alle fünf Sekunden einen
 * Umschlag an jedes Geschwister — und die Mailbox füllte sich mit Anfragen.
 */
const dmGefragt = new Map<string, number>()
const DM_ANFRAGE_ABSTAND_MS = 60_000

/**
 * Bittet die eigenen Zweitgeräte um das Chatgeheimnis eines Direktchats.
 *
 * Wird nur gerufen, wenn dieses Gerät beim Lesen merkt, dass es keines hat
 * (`dmZiele` fällt dann auf die alte, abgeleitete Mailbox zurück). Die Antwort
 * kommt als gewöhnliche Schlüsselzustellung in der Geräte-Mailbox an; dieses
 * Gerät liest sie beim nächsten Durchlauf. Die Gegenseite sieht davon nichts —
 * die Anfrage geht ausschließlich an das eigene Konto.
 */
export async function fordereDmGeheimnis(eigeneId: number, peerId: number): Promise<boolean> {
  if (!Number.isInteger(eigeneId) || eigeneId <= 0) return false
  if (!Number.isInteger(peerId) || peerId <= 0 || peerId === eigeneId) return false
  try {
    if (await ablage.liesDmGeheimnis(peerId)) return false
  } catch {
    return false
  }
  const jetzt = Date.now()
  // Je Konto und Peer: ein Kontowechsel im selben Tab darf die Nachfrage des
  // vorigen Kontos nicht für eine Minute unterdrücken.
  const marke = `${eigeneId}:${peerId}`
  if (jetzt - (dmGefragt.get(marke) ?? 0) < DM_ANFRAGE_ABSTAND_MS) return false
  dmGefragt.set(marke, jetzt)

  try {
    const meins = await eigenesGeraet()
    const nutzlast = await signiereNutzlast(await deriveBlindMailboxId(eigeneId, peerId), eigeneId, {
      typ: DM_ANFRAGE_TYP,
      v: 1,
      peerId,
      eigeneId,
      geraet: meins.kennung,
    })
    const kontext: GruppenKontext = {
      groupId: 0,
      blindMailboxId: '',
      eigeneId,
      mitglieder: [],
      istEigentuemer: false,
    }
    await anJedesGeraet(kontext, [eigeneId], JSON.stringify(nutzlast), DM_ANFRAGE_TYP)
    return true
  } catch {
    // Kein Netz oder kein Geschwistergerät. Der nächste Versuch nach der Frist.
    dmGefragt.delete(marke)
    return false
  }
}

/**
 * Beantwortet eine solche Anfrage — nur mit dem **aktuellen** Geheimnis, nur
 * an das genannte eigene Gerät, und nur wenn es im Verzeichnis steht.
 *
 * Die Prüfung ist dieselbe wie beim Empfang: Beglaubigung gegen die
 * abgeleitete Gesprächskennung, Downgrade-Schranke für Konten mit
 * Signaturschlüssel, und der Absender muss das eigene Konto sein. Ein
 * untergeschobener Umschlag (jeder kennt den öffentlichen Geräteschlüssel)
 * könnte sonst ein fremdes Gerät mit einem erfundenen Geheimnis versorgen.
 */
export async function beantworteDmAnfrage(eigeneId: number, roh: Record<string, unknown>): Promise<boolean> {
  const peerId = Number(roh.peerId)
  const geraet = typeof roh.geraet === 'string' ? roh.geraet : ''
  if (!Number.isInteger(peerId) || peerId <= 0 || peerId === eigeneId || !geraet) return false

  const pruefung = await pruefeNutzlast(await deriveBlindMailboxId(eigeneId, peerId), roh)
  if (pruefung.art === 'gefaelscht') return false
  if (pruefung.art === 'geprueft' && Number(pruefung.vonKonto) !== Number(eigeneId)) return false
  if (pruefung.art === 'unsigniert' && (await kontoNutztSignaturen(eigeneId))) return false

  const eintrag = await ablage.liesDmGeheimnis(peerId)
  if (!eintrag?.geheimnis) return false

  const bekannt = await geraeteVon(eigeneId).catch(() => [])
  if (!bekannt.some((g) => g.device_id === geraet)) return false

  const darfSelbst = darfDmGeheimnisErzeugen(eigeneId, peerId)
  const beleg = darfSelbst ? undefined : eintrag.beleg
  // Wer nicht erzeugen darf, kann nur den **Originalumschlag** der Gegenseite
  // weitergeben. Selbst unterschrieben trüge er die grössere Kennung und würde
  // beim Empfänger abgewiesen.
  if (!darfSelbst && !beleg) return false

  const { eigene } = await verteileDmGeheimnis(eigeneId, peerId, eintrag.geheimnis, {
    nurEigene: [geraet],
    beleg,
  })
  if (eigene.length > 0) {
    await ablage.merkeDmVerteilung(peerId, eintrag.verteiltAm ?? new Date().toISOString(), eigene)
  }
  return eigene.length > 0
}

/**
 * Eigene Geräte, die das Chatgeheimnis noch nicht bekommen haben.
 *
 * Das eigene Gerät steht nicht in der Liste — es hat es. Ein **wartendes**
 * Gerät fehlt hier ebenfalls, weil `geraeteVon` es noch nicht führt: es bekommt
 * nichts, bis es freigegeben ist, und danach steht es hier als fehlend.
 */
async function eigeneGeraeteOhne(
  eigeneId: number,
  bekannt: readonly string[],
): Promise<string[]> {
  const meins = await eigenesGeraet()
  let kennungen: string[]
  try {
    kennungen = (await geraeteVon(eigeneId)).map((g) => g.device_id)
  } catch {
    return []
  }
  const hat = new Set(bekannt)
  return kennungen.filter((id) => id !== meins.kennung && !hat.has(id))
}

/**
 * Nimmt ein zugestelltes Chatgeheimnis an. Gibt `true`, wenn es neu war.
 *
 * Die Kennung im Umschlag ist die des **Absenders**, nicht meine: er schrieb
 * `peerId` aus seiner Sicht. Für mich ist die Gegenstelle deshalb sein
 * `eigeneId` — es sei denn, der Umschlag kommt von meinem eigenen Zweitgerät,
 * dann stimmt seine Sicht mit meiner überein.
 *
 * **Drei Schranken, und jede hat einen eigenen Angriff hinter sich:**
 *
 * 1. *Beglaubigt.* Ein Hybridumschlag hat keinen Absenderkopf — jeder, der
 *    meinen öffentlichen Geräteschlüssel kennt, kann mir einen schicken. Ohne
 *    Prüfung könnte ein Dritter sich als meine Gegenstelle ausgeben und mein
 *    Gespräch in eine Mailbox umlenken, die er selbst gewählt hat. Lesen
 *    könnte er dort nichts (die Nachrichten bleiben im Ratchet), aber die
 *    Gegenstelle sähe nie wieder eine — ein stiller Abriss.
 * 2. *Nur von der kleineren Kennung.* Dieselbe Regel wie beim Erzeugen, hier
 *    von der anderen Seite. Ohne sie könnten beide Seiten gleichzeitig eines
 *    erzeugen, jede das eigene behalten, und das Gespräch zerfiele in zwei
 *    Hälften — genau das, was die Regel verhindern soll.
 * 3. *Überschreibt nie.* Wer schon eines hat, behält es.
 */
export async function nimmDmGeheimnis(eigeneId: number, roh: Record<string, unknown>): Promise<boolean> {
  const geheimnis = typeof roh.geheimnis === 'string' ? roh.geheimnis : ''
  const absender = Number(roh.eigeneId)
  const gemeint = Number(roh.peerId)
  if (!geheimnis || !Number.isInteger(absender) || !Number.isInteger(gemeint)) return false
  // Der Absender muss der sein, der erzeugen darf. Sonst wäre die Regel eine
  // Bitte und keine Schranke.
  if (!darfDmGeheimnisErzeugen(absender, gemeint)) return false

  const gegenstelle = absender === eigeneId ? gemeint : absender
  if (!Number.isInteger(gegenstelle) || gegenstelle <= 0 || gegenstelle === eigeneId) return false

  // Gebunden an die abgeleitete Gesprächskennung, wie beim Unterschreiben.
  const pruefung = await pruefeNutzlast(await deriveBlindMailboxId(eigeneId, gegenstelle), roh)
  if (pruefung.art === 'gefaelscht') return false
  if (pruefung.art === 'geprueft') {
    // Die Unterschrift gilt — aber wem? Wer unterschrieben hat, muss auch der
    // sein, der sich im Paket nennt.
    if (pruefung.vonKonto !== absender) return false
  } else if (await kontoNutztSignaturen(absender)) {
    // Die Downgrade-Schranke: wer beglaubigen kann, muss es auch. Ohne sie
    // nähme ein Fälscher die Unterschrift einfach weg.
    return false
  }

  /*
   * Das Gespräch in den örtlichen Speicher — hier und nirgends sonst.
   *
   * Seit Stufe 6b führt der Server keine Liste mehr, mit wem dieses Konto
   * schreibt. Dieser Umschlag ist der Moment, in dem ein bis dahin unbekanntes
   * Gegenüber zum Gesprächspartner wird; wird er hier nicht festgehalten,
   * abonniert dieses Gerät die neue Mailbox nie und die erste Nachricht kommt
   * nirgends an.
   *
   * Ohne Namen: der Umschlag trägt eine Konto-Id, und das soll er auch. Ein
   * Anzeigename darin wäre ein Feld, das ein Absender frei wählt — und damit
   * der Weg, sich in einer Kontaktliste als jemand anderes auszugeben. Den
   * Namen holt `fuelleNamenNach` aus der Kontaktliste.
   */
  try {
    const { merkeGespraech } = await import('../gespraechsListe')
    await merkeGespraech(gegenstelle, {})
  } catch {
    // Verschlossener Messenger oder volle Ablage. Das Geheimnis selbst zählt
    // mehr als die Zeile daneben; der nächste `dmZiele` holt sie nach.
  }

  const vorhanden = await ablage.liesDmGeheimnis(gegenstelle)
  const jetzt = new Date().toISOString()
  await ablage.schreibeDmGeheimnis({
    peerId: gegenstelle,
    geheimnis,
    erzeugtAm: jetzt,
    // Beim Empfänger steht die Marke sofort: er hat es ja. Sie fragt „wissen
    // es beide?", nicht „habe ich es verschickt?".
    verteiltAm: jetzt,
    // Der geprüfte Umschlag, für die Weitergabe an ein eigenes Gerät. Er
    // wird nicht neu unterschrieben — die Unterschrift gehört der Gegenseite.
    beleg: roh,
  })
  if (vorhanden) return false

  const neu = await mailboxAusGeheimnis(geheimnis)
  const token = await nachweisAusGeheimnis(geheimnis)
  merkeMailboxNachweis(neu, token)
  abonniereMailbox(neu, token)
  try {
    // Ohne Nachweis bliebe die Mailbox für dieses Gerät offen — lesen könnte
    // es dort, aber der Server liesse auch jeden anderen hinein, der die
    // Kennung errät. Scheitert es, holt es der nächste `dmZiele` nach.
    await registriereMailbox(neu, token)
  } catch {
    /* nachholbar */
  }
  return true
}

/**
 * Wohin ein Direktchat sendet und woraus er liest — das Gegenstück zu
 * `gruppenZiele`.
 *
 * Mit `erzeuge` entsteht eines, wenn dieses Konto darf und es noch keines
 * gibt; verteilt wird es dann an beide Seiten. Was daran scheitert, hält das
 * Senden nicht auf: ohne Geheimnis bleibt es bei der alten Kennung, und die
 * funktioniert.
 *
 * **Nur beim Senden, nie beim Lesen.** Erzeugen heisst verteilen, und
 * verteilen heisst Umschläge an die Geräte der Gegenstelle. Täte das schon
 * das Öffnen eines Chats, würde Nachsehen zu einem sichtbaren Vorgang — wer
 * ein Gespräch nur aufschlägt und nichts schreibt, hinterliesse trotzdem eine
 * Spur bei der Gegenseite. Dieselbe Trennung hat die Gruppe: dort entsteht
 * das Geheimnis in `verschluesseleFuerGruppe`, und `gruppenZiele` liest nur,
 * was schon da ist.
 *
 * **Die erste Kontaktaufnahme bleibt sichtbar.** Sie läuft über die
 * Geräte-Mailbox der Gegenstelle, und dort steht, dass *irgendjemand* Konto 42
 * angeschrieben hat — nicht wer. Danach zieht das Gespräch um. Dasselbe
 * Restleck hat Signal, und es lässt sich ohne einen dritten Dienst nicht
 * schliessen.
 */
export async function dmZiele(
  eigeneId: number,
  peerId: number,
  altKennung: string,
  { erzeuge = false }: { erzeuge?: boolean } = {},
): Promise<GruppenZiele> {
  if (!Number.isInteger(eigeneId) || !Number.isInteger(peerId) || peerId <= 0) {
    return beiDerAltenKennung(altKennung)
  }

  // Wie bei `gruppenZiele`: eine unerreichbare Ablage nimmt den Umzug, nie
  // das Lesen.
  let eintrag: DmGeheimnis | null
  try {
    eintrag = await ablage.liesDmGeheimnis(peerId)
    if (!eintrag && erzeuge && darfDmGeheimnisErzeugen(eigeneId, peerId)) {
      const roh = randomBytes(SCHLUESSEL_BYTES)
      const neu = bytesToBase64(roh)
      roh.fill(0)
      await ablage.schreibeDmGeheimnis({
        peerId,
        geheimnis: neu,
        erzeugtAm: new Date().toISOString(),
        verteiltAm: null,
      })
      // Nicht das gerade erzeugte nehmen, sondern das abgelegte: ein
      // gleichzeitiger zweiter Aufruf hat vielleicht gewonnen.
      eintrag = await ablage.liesDmGeheimnis(peerId)
    }

    // Solange die Gegenstelle es nicht hat, wird bei jedem Versand erneut
    // zugestellt. Der Fall dahinter ist die allererste Nachricht: der Server
    // lässt einen Steuerumschlag in eine fremde Geräte-Mailbox nur bei
    // bestehender Beziehung durch, und die entsteht erst mit dieser Nachricht.
    if (eintrag && erzeuge && !eintrag.verteiltAm) {
      let gegenstelle = 0
      let eigene: string[] = []
      try {
        ;({ gegenstelle, eigene } = await verteileDmGeheimnis(
          eigeneId,
          peerId,
          eintrag.geheimnis,
        ))
      } catch {
        // Kein Netz. Nächster Versand, nächster Versuch.
      }
      if (gegenstelle > 0) {
        const wann = new Date().toISOString()
        await ablage.merkeDmVerteilung(peerId, wann, eigene)
        eintrag = { ...eintrag, verteiltAm: wann, verteiltAn: eigene }
      }
    } else if (eintrag && erzeuge) {
      // Die Gegenstelle hat es — **die eigenen Geräte darum noch lange nicht.**
      // Bis 10/2026 lief die Verteilung genau einmal im Leben eines Chats. Ein
      // danach gekoppeltes oder zurückgesetztes Gerät las die neue Mailbox und
      // sah nichts, während jedes andere Gerät ausschließlich dorthin schrieb.
      const fehlend = await eigeneGeraeteOhne(eigeneId, eintrag.verteiltAn ?? [])
      // Erzeugen darf nur die kleinere Kennung — hat die Gegenseite es
      // erzeugt, geht ihr **Originalumschlag** weiter, sonst nichts. Selbst
      // unterschrieben trüge er die grössere Kennung und würde abgewiesen; das
      // Gerät als bedient zu vermerken hiesse, es für immer ohne Geheimnis zu
      // lassen (bis 20.10.2026).
      const darfSelbst = darfDmGeheimnisErzeugen(eigeneId, peerId)
      const beleg = darfSelbst ? undefined : eintrag.beleg
      if (fehlend.length > 0 && (darfSelbst || beleg)) {
        const { eigene } = await verteileDmGeheimnis(eigeneId, peerId, eintrag.geheimnis, {
          nurEigene: fehlend,
          beleg,
        })
        if (eigene.length > 0) {
          await ablage.merkeDmVerteilung(peerId, eintrag.verteiltAm ?? new Date().toISOString(), eigene)
          eintrag = { ...eintrag, verteiltAn: [...(eintrag.verteiltAn ?? []), ...eigene] }
        }
      }
    }
  } catch {
    return beiDerAltenKennung(altKennung)
  }

  // **Kein Umzug ohne Zustellung.** Zöge das Gespräch schon vorher um, ginge
  // die Nachricht in eine Mailbox, die die Gegenseite weder kennt noch je
  // erfährt — und die Zustellung, die es ihr sagen würde, käme nie zustande.
  // Ein Ring, der sich nicht von selbst öffnet.
  if (!eintrag?.geheimnis || !eintrag.verteiltAm) return beiDerAltenKennung(altKennung)

  const neu = await mailboxAusGeheimnis(eintrag.geheimnis)
  const token = await nachweisAusGeheimnis(eintrag.geheimnis)
  merkeMailboxNachweis(neu, token)
  abonniereMailbox(neu, token)
  try {
    await registriereMailbox(neu, token)
  } catch {
    // Wie bei der Gruppe: lieber ohne Nachweis senden als gar nicht.
  }
  if (neu === altKennung) return { senden: neu, lesen: [neu], nachweis: token }

  merkeUmzug(altKennung, neu)
  abonniereMailbox(altKennung, mailboxNachweis(altKennung))
  return { senden: neu, lesen: [neu, altKennung], nachweis: token }
}

/** Vergisst die Drossel der Chatgeheimnis-Nachfragen im Arbeitsspeicher. */
export function leereDmAnfragen(): void {
  dmGefragt.clear()
}
