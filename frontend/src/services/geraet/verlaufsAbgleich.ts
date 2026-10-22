/**
 * Verlauf an ein frisch freigegebenes eigenes Gerät.
 *
 * Ein Gerät hat seinen eigenen Ratchet-Schlüssel. Was vor seiner Freigabe
 * geschrieben wurde, liegt in dessen Mailbox nur als Umschlag für die damals
 * freigegebenen Geräte; das neue Gerät kann es nicht öffnen. Ohne Übergabe
 * beginnt sein Verlauf an dem Tag, an dem es freigegeben wurde — genau die
 * Lücke, die auf zwei Geräten wie zwei verschiedene Messenger aussieht.
 *
 * Deshalb schickt ein freigegebenes Gerät beim Freigeben seinen lokalen
 * Klartextverlauf an das neue Gerät. Verschlüsselt wird gegen dessen
 * Geräteschlüssel, unterschrieben mit dem Signaturschlüssel des Absenders und
 * gebunden an die Geräte-Mailbox. Der Server sieht nur Chiffrat.
 *
 * **Nur Klartext, keine Schlüssel.** Das neue Gerät baut seine Sitzungen
 * weiter selbst auf; es bekommt nur, was dieses Gerät bereits gelesen hat.
 *
 * Die Nachricht reist als ein Umschlag je Mailbox. Ein Umschlag darf 1 MiB
 * fassen; deshalb wird die Zahl der Nachrichten je Mailbox so lange halbiert,
 * bis Chiffrat und Unterschrift hineinpassen. Anhänge mit eingebetteten
 * `data:`-Daten fallen dabei auf ihren Verweis zusammen — ein halber Verlauf
 * mit Verweisen ist mehr wert als gar keiner, und der Inhalt ist über
 * `mediaId` weiterhin abrufbar.
 */

import { relayE2eeEnvelope, type E2eeGeraetItem } from '@/api/social'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import {
  deriveBlindMailboxId,
  deriveUserDeviceMailboxId,
  encryptE2eeHybrid,
} from '../e2eeCrypto'
import { ladeGespraeche } from '../gespraechsListe'
import { ablage } from '../gruppen/gruppenAblage'
import { uebernimmDmGeheimnis } from '../gruppen/dmGeheimnis'
import { eigenesGeraet } from './geraeteAblage'
import { geraeteVon } from './geraeteVerzeichnis'
import {
  listeLokaleMailboxen,
  loadLocalMessages,
  mischeVerlauf,
  saveLocalMessages,
  sortMessagesChronologically,
  type LocalStoredMessage,
} from '../messengerLocalStore'
import { pruefeNutzlast, signiereNutzlast } from '../nutzlastSignatur'

export const VERLAUF_SYNC_TYP = 'history_sync'

/**
 * Welche Geschwister schon einmal einen Verlauf bekommen haben.
 *
 * Der Merker liegt beim **Absender**, nicht beim Empfänger: nur der weiß, ob
 * er etwas geschickt hat. Ein neues Gerät ohne Eintrag bekommt beim nächsten
 * Messenger-Start eine Übergabe; danach nie wieder. Beide Geräte dürfen
 * senden, denn ihre Verläufe können verschiedene Lücken haben — das
 * Zusammenführen auf der Empfängerseite ist idempotent.
 */
const GESENDET_SCHLUESSEL = 'msm:history_sent'

function gesendetAn(konto: number, geraet: string): boolean {
  try {
    return Boolean(window.localStorage.getItem(`${GESENDET_SCHLUESSEL}:${konto}:${geraet}`))
  } catch {
    return false
  }
}

function merkeGesendet(konto: number, geraet: string): void {
  try {
    window.localStorage.setItem(`${GESENDET_SCHLUESSEL}:${konto}:${geraet}`, new Date().toISOString())
  } catch {
    // Ohne Ablage geht die Übergabe beim nächsten Start noch einmal; das
    // Zusammenführen kostet dann nur Zeit, keinen Inhalt.
  }
}

/**
 * Wie viel Klartext ein Paket höchstens tragen darf.
 *
 * Der Umschlag fasst 1 MiB Zeichen; Base64, AES-GCM und die äußere Hülle
 * kosten etwa das Vierfache. 180 KB Klartext bleiben mit Sicherheit darunter.
 */
const MAX_KLARTEXT_BYTES = 180_000

/** Wie viele Nachrichten je Umschlag höchstens mitgehen. */
const CHUNK = 200

/**
 * Wie viele Umschläge je Mailbox höchstens.
 *
 * 25 × 200 = 5000 Nachrichten. Das deckt auch einen über Wochen gewachsenen
 * Chat; alles darüber würde beim Gegenüber ohnehin niemand mehr lesen und
 * kostet nur Relais-Zeit.
 */
const MAX_TEILE = 25

/** Eine einzelne Nachricht über dieser Grenze ist ein Anhang, kein Verlauf. */
const MAX_NACHRICHT_BYTES = 40_000

function byteLaenge(text: string): number {
  return new TextEncoder().encode(text).length
}

function endung(geraetId: string): string {
  const sauber = (geraetId || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(-16)
  return sauber || 'geraet'
}

/**
 * Eine Nachricht ohne eingebettete Anhangsdaten.
 *
 * `dataUrl` ist eine Kopie des Inhalts für die sofortige Anzeige auf dem
 * sendenden Gerät. Sie kann mehrere Megabyte groß sein und gehört nicht in
 * einen Verlaufsumschlag; der Verweis (`mediaId`, `paketSchluessel`) genügt,
 * um den Inhalt auf dem neuen Gerät zu laden.
 */
function kompakt(nachricht: LocalStoredMessage): LocalStoredMessage | null {
  if (!nachricht || typeof nachricht !== 'object') return null
  if (typeof nachricht.text !== 'string') return null
  if (!Number.isFinite(Number(nachricht.id)) || !Number.isFinite(Number(nachricht.senderId))) {
    return null
  }
  if (typeof nachricht.createdAt !== 'string') return null
  if (typeof nachricht.isSelf !== 'boolean') return null
  // Was nie hinausging, gehört auch nicht in den Verlauf eines anderen
  // Geräts: es stünde dort für immer auf „wird gesendet“.
  if (nachricht.status === 'queued' || nachricht.status === 'failed') return null

  const kopie: LocalStoredMessage = { ...nachricht }
  delete kopie.isSystem
  // Gelöscht bleibt gelöscht. Der Grabstein darf mitreisen, sein Inhalt nicht.
  if (kopie.isDeleted) {
    kopie.text = ''
    delete kopie.originalText
  }
  const alsFelder = kopie as unknown as Record<string, unknown>
  for (const feld of [
    'imageAttachment',
    'fileAttachment',
    'audioAttachment',
    'videoNoteAttachment',
  ] as const) {
    const anhang = alsFelder[feld]
    if (anhang && typeof anhang === 'object') {
      const ohneDaten = { ...(anhang as Record<string, unknown>) }
      delete ohneDaten.dataUrl
      alsFelder[feld] = ohneDaten
    }
  }
  if (kopie.isDeleted) {
    delete alsFelder.imageAttachment
    delete alsFelder.fileAttachment
    delete alsFelder.audioAttachment
    delete alsFelder.videoNoteAttachment
    delete alsFelder.noteAttachment
    delete alsFelder.calendarAttachment
    delete alsFelder.stickerAttachment
    delete alsFelder.storyReply
  }
  if (byteLaenge(JSON.stringify(kopie)) > MAX_NACHRICHT_BYTES) return null
  return kopie
}

async function packeAuswahl(
  mid: string,
  auswahl: readonly LocalStoredMessage[],
  zielSchluessel: string,
  dmGeheimnis?: { peerId: number; geheimnis: string; beleg?: Record<string, unknown> },
): Promise<{ umschlag: string; anzahl: number } | null> {
  let n = auswahl.length
  while (n >= 1) {
    const teil = auswahl.slice(-n)
    const klartext = JSON.stringify({
      v: 1,
      mailbox: mid,
      nachrichten: teil,
      ...(dmGeheimnis ? { dmGeheimnis } : {}),
    })
    if (byteLaenge(klartext) <= MAX_KLARTEXT_BYTES) {
      const umschlag = await encryptE2eeHybrid(klartext, zielSchluessel)
      if (umschlag.length <= 900_000) return { umschlag, anzahl: n }
    }
    n = n <= 1 ? 0 : Math.max(1, Math.floor(n / 2))
  }
  return null
}

/**
 * Schickt den lokalen Verlauf an ein freigegebenes Gerät desselben Kontos.
 *
 * Gibt die Zahl der verschickten Mailbox-Pakete zurück. Wirft nicht bei einem
 * einzelnen Paket: eine zu große oder kaputte Mailbox hält die übrigen nicht
 * auf. Der Aufrufer entscheidet, ob er das Ergebnis meldet.
 */
export async function sendeVerlaufAnGeraet(ziel: E2eeGeraetItem): Promise<number> {
  const konto = angemeldetesKonto()
  if (!konto || !ziel?.device_id || !ziel.public_key) return 0
  const meins = await eigenesGeraet()
  if (meins.kennung === ziel.device_id) return 0

  const mailboxen = await listeLokaleMailboxen()
  if (mailboxen.length === 0) return 0
  const geraeteMailbox = await deriveUserDeviceMailboxId(konto)

  /**
   * Welche Mailbox zu welchem Gegenüber gehört.
   *
   * Das Chatgeheimnis liegt nicht in der Mailbox, sondern in der Ablage dieses
   * Kontos. Es muss mit: ohne es liest das neue Gerät die alte, abgeleitete
   * Mailbox und sieht keine Nachricht, die nach seiner Freigabe in der neuen
   * ankommt. Bis ein Geschwistergerät seine Nachfrage beantwortet, ist es dann
   * stumm — und genau dieses Fenster machte den Messenger unbrauchbar.
   */
  const partnerJeMailbox = new Map<string, number>()
  try {
    for (const gespraech of (await ladeGespraeche()).values()) {
      if (!gespraech?.userId) continue
      const abgeleitet = await deriveBlindMailboxId(konto, gespraech.userId)
      partnerJeMailbox.set(gespraech.blindMailboxId || abgeleitet, gespraech.userId)
      partnerJeMailbox.set(abgeleitet, gespraech.userId)
    }
  } catch {
    // Verschlossener Messenger: dann eben nur der Verlauf, ohne Geheimnis.
  }

  let gesendet = 0
  for (const mid of mailboxen) {
    try {
      const nachrichten = await loadLocalMessages(mid)
      if (nachrichten.length === 0) continue
      const peerId = partnerJeMailbox.get(mid)
      let dmGeheimnis:
        | { peerId: number; geheimnis: string; beleg?: Record<string, unknown> }
        | undefined
      if (peerId) {
        try {
          const eintrag = await ablage.liesDmGeheimnis(peerId)
          if (eintrag?.geheimnis) {
            dmGeheimnis = {
              peerId,
              geheimnis: eintrag.geheimnis,
              ...(eintrag.beleg ? { beleg: eintrag.beleg } : {}),
            }
          }
        } catch {
          // Ohne Geheimnis geht der Verlauf trotzdem.
        }
      }
      const brauchbar = sortMessagesChronologically([...nachrichten])
        .map(kompakt)
        .filter((m): m is LocalStoredMessage => m !== null)
      if (brauchbar.length === 0) continue

      // In Teilen, nicht nur die letzten 200: der gemeldete Verlauf riss
      // genau dort ab, wo das eine Paket aufhörte. Jeder Teil trägt das
      // Chatgeheimnis nur beim ersten Mal.
      let rest = brauchbar
      let teil = 0
      while (rest.length > 0 && teil < MAX_TEILE) {
        const auswahl = rest.slice(-CHUNK)
        const gepackt = await packeAuswahl(
          mid,
          auswahl,
          ziel.public_key,
          teil === 0 ? dmGeheimnis : undefined,
        )
        if (!gepackt) {
          // Eine einzelne, zu große Nachricht fällt weg; der Rest geht mit.
          rest = rest.slice(0, rest.length - 1)
          continue
        }

        const aussen = await signiereNutzlast(geraeteMailbox, konto, {
          type: VERLAUF_SYNC_TYP,
          v: 1,
          ziel: ziel.device_id,
          geraet: meins.kennung,
          paket: gepackt.umschlag,
        })
        const umschlag = await encryptE2eeHybrid(
          JSON.stringify(aussen),
          ziel.public_key,
          meins.paar.publicKeyJwk,
        )
        await relayE2eeEnvelope({
          blind_mailbox_id: geraeteMailbox,
          ciphertext_envelope: umschlag,
          client_uuid: `history-${endung(ziel.device_id)}-${Date.now()}-${gesendet}-${teil}`,
          is_control: true,
          control_type: VERLAUF_SYNC_TYP,
        })
        gesendet += 1
        rest = rest.slice(0, rest.length - gepackt.anzahl)
        teil += 1
      }
    } catch {
      // Naechste Mailbox. Ein Paket ist ein Zusatz, kein Grund, die Freigabe
      // rueckgaengig zu machen.
    }
  }
  return gesendet
}

/**
 * Holt den Abgleich für alle freigegebenen Geschwister nach.
 *
 * Wird beim Öffnen des Messengers gerufen. Der Fall dahinter: ein Gerät wurde
 * freigegeben, bevor es diese Übergabe gab — dann steht es in der Liste, hat
 * aber nie einen Verlauf bekommen. Der Merker verhindert, dass jeder Start
 * denselben Verlauf erneut schickt.
 *
 * Auch das **eigene** Gerät kann etwas beizutragen haben: Web und App haben
 * unterschiedliche Lücken. Deshalb senden beide Richtungen einmal; das
 * Zusammenführen ist idempotent.
 */
export async function sendeVerlaufAnNeueGeraete(): Promise<number> {
  const konto = angemeldetesKonto()
  if (!konto) return 0
  let meins: Awaited<ReturnType<typeof eigenesGeraet>>
  try {
    meins = await eigenesGeraet()
  } catch {
    return 0
  }
  const geschwister = await geraeteVon(konto)
  let gesendet = 0
  for (const geraet of geschwister) {
    if (geraet.device_id === meins.kennung) continue
    if (gesendetAn(konto, geraet.device_id)) continue
    try {
      const pakete = await sendeVerlaufAnGeraet(geraet)
      if (pakete > 0) {
        merkeGesendet(konto, geraet.device_id)
        gesendet += 1
      }
    } catch {
      // Beim nächsten Start erneut. Kein Grund, den Messenger zu behindern.
    }
  }
  return gesendet
}

/**
 * Nimmt ein Verlaufspaket aus der eigenen Geräte-Mailbox an.
 *
 * Drei Schranken:
 * 1. Es ist an **dieses** Gerät adressiert (`ziel`).
 * 2. Es ist von einem Gerät des eigenen Kontos unterschrieben, und die
 *    Unterschrift deckt die Geräte-Mailbox als Bindung.
 * 3. Das innere Paket lässt sich mit dem eigenen Schlüsselbund öffnen.
 *
 * Erst dann wird gemischt. Ein gefälschtes Paket kann damit keinen Verlauf
 * einschleusen; es kostet höchstens einen fehlgeschlagenen Öffnungsversuch.
 */
export async function verarbeiteVerlaufsAbgleich(
  roh: Record<string, unknown>,
  eigeneId: number,
  entsiegle: (umschlag: string) => Promise<string>,
): Promise<number> {
  if (roh.type !== VERLAUF_SYNC_TYP) return 0
  const konto = angemeldetesKonto()
  if (!konto || Number(konto) !== Number(eigeneId)) return 0

  const meins = await eigenesGeraet()
  if (String(roh.ziel || '') !== meins.kennung) return 0

  const geraeteMailbox = await deriveUserDeviceMailboxId(eigeneId)
  const pruefung = await pruefeNutzlast(geraeteMailbox, roh)
  if (pruefung.art !== 'geprueft' || Number(pruefung.vonKonto) !== Number(eigeneId)) {
    return 0
  }
  const vonGeraet = typeof roh.geraet === 'string' ? roh.geraet : ''
  if (!vonGeraet) return 0
  const geschwister = await geraeteVon(eigeneId)
  if (!geschwister.some((g) => g.device_id === vonGeraet)) return 0

  const paketText = await entsiegle(String(roh.paket || ''))
  let paket: { mailbox?: unknown; nachrichten?: unknown; dmGeheimnis?: unknown }
  try {
    paket = JSON.parse(paketText)
  } catch {
    return 0
  }
  const dm = paket.dmGeheimnis as
    | { peerId?: unknown; geheimnis?: unknown; beleg?: unknown }
    | undefined
  if (dm && typeof dm.geheimnis === 'string' && Number.isInteger(Number(dm.peerId))) {
    try {
      const beleg =
        dm.beleg && typeof dm.beleg === 'object' ? (dm.beleg as Record<string, unknown>) : undefined
      await uebernimmDmGeheimnis(Number(dm.peerId), dm.geheimnis, beleg)
    } catch {
      // Der Verlauf darunter ist trotzdem brauchbar.
    }
  }
  const mid = typeof paket.mailbox === 'string' ? paket.mailbox : ''
  const rohe = Array.isArray(paket.nachrichten) ? paket.nachrichten : []
  if (!mid || rohe.length === 0) return 0

  const nachrichten = rohe
    .map((m) => kompakt(m as LocalStoredMessage))
    .filter((m): m is LocalStoredMessage => m !== null)
    .slice(-500)
  if (nachrichten.length === 0) return 0

  const vorhanden = await loadLocalMessages(mid)
  const zusammen = mischeVerlauf(vorhanden, nachrichten)
  await saveLocalMessages(mid, zusammen)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('msm:messages-updated', { detail: { blind_mailbox_id: mid } }),
    )
  }
  return nachrichten.length
}
