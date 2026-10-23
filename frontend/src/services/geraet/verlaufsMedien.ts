/**
 * Anhänge gehören zum Verlauf: sie ziehen als eigene Pakete mit.
 *
 * Eine Nachricht trägt nur einen Zeiger auf den Anhang; der Server hält den
 * Blob 30 Tage. Wer nur den Zeiger an ein neues Gerät weitergibt, übergibt
 * nach Ablauf eine Nachricht mit einem Bild, das es nicht mehr gibt. Deshalb
 * gehen die Daten selbst mit — aus der lokalen Ablage, in Stücken von
 * 160 000 Zeichen, mit Größe und Prüfsumme.
 *
 * Fertig ist ein Anhang erst, wenn das Ziel ihn zusammengesetzt, geprüft und
 * abgelegt hat. Erst dann kommt sein ACK zurück, und erst dann merkt sich der
 * Absender ihn als übergeben. Ein Teilfehler läßt die Übergabe offen; der
 * nächste Lauf schickt dieselben Stücke mit derselben Kennung noch einmal.
 *
 * Ein geloeschter Anhang bleibt geloescht: der Grabstein der Medienkennung
 * weist jeden spaeteren Transfer ab, auch von einem Geraet, das die Loeschung
 * nie gesehen hat. Es gibt dann kein ACK — die Datei ist hier nicht
 * angekommen, sie ist bewusst weg.
 */

import { utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'

import { relayE2eeEnvelope, type E2eeGeraetItem } from '@/api/social'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { deriveUserDeviceMailboxId, encryptE2eeHybrid } from '../e2eeCrypto'
import { sichereMedium, zeigerVon } from '../medienSicherung'
import type { MedienZeiger } from '../medienKrypto'
import {
  holeMedienTeile,
  istMediumGeloescht,
  legeMedienTeilAb,
  leseMedium,
  loescheMedienTeile,
  speichereMedium,
  type LocalStoredMessage,
} from '../messengerLocalStore'
import { signiereNutzlast } from '../nutzlastSignatur'
import { eigenesGeraet } from './geraeteAblage'
import { geraeteVon } from './geraeteVerzeichnis'

export const VERLAUF_MEDIA_TYP = 'history_media'
export const VERLAUF_MEDIA_ACK_TYP = 'history_media_ack'

/** Dieselbe Grenze wie beim Verlauf: das Relais nimmt 1 MiB Zeichen. */
export const MAX_UMSCHLAG_ZEICHEN = 1_000_000

/** Ein Anhang der Historie: so gross darf eine Datei hoechstens sein. */
const MAX_MEDIA_ZEICHEN = 12_000_000
/** So viel Anhangsdaten (data-URL-Zeichen) geht ein Uebergabelauf hoechstens. */
const MAX_MEDIA_GESAMT = 64_000_000
/** So viele Zeichen traegt ein Medienpaket. */
const MEDIEN_TEIL_ZEICHEN = 160_000
/** Ein Uebertragungsfenster; aeltere Pakete werden verworfen. */
const FENSTER_MS = 24 * 60 * 60 * 1000
const GESENDETE_MEDIEN = 'msm:history_media_done'

export function endung(geraetId: string): string {
  const sauber = (geraetId || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(-16)
  return sauber || 'geraet'
}

function mediumGesendet(konto: number, geraet: string, mediaId: string): boolean {
  try {
    return Boolean(
      window.localStorage.getItem(`${GESENDETE_MEDIEN}:${konto}:${geraet}:${mediaId}`),
    )
  } catch {
    return false
  }
}

export function merkeMediumGesendet(konto: number, geraet: string, mediaId: string): void {
  try {
    window.localStorage.setItem(
      `${GESENDETE_MEDIEN}:${konto}:${geraet}:${mediaId}`,
      new Date().toISOString(),
    )
  } catch {
    // Ohne Ablage geht der Anhang beim naechsten Lauf noch einmal; das ist
    // nur Zeit, kein Fehler.
  }
}

export function kennungInOrdnung(wert: unknown): wert is string {
  return typeof wert === 'string' && /^[A-Za-z0-9._:-]{1,120}$/.test(wert)
}

/** Ein Anhang, den die Uebergabe mitnehmen will. */
export interface MedienAuftrag {
  zeiger: MedienZeiger
  bindung: { absenderId: number; blindMailboxId: string }
  name: string | null
}

/**
 * Die Anhaenge einer Nachricht mit ihren Zeigern — auch die eigenen.
 *
 * Ein eigenes Geraet, das den Anhang nicht hochgeladen hat, hat ihn nicht:
 * es kennt nur den Zeiger. Fuer die Uebergabe zaehlt das Geraet, auf dem die
 * Datei liegt, nicht wer die Nachricht geschrieben hat.
 */
export function anhaengeSammeln(
  nachrichten: readonly LocalStoredMessage[],
  mailbox: string,
  ziel: Map<string, MedienAuftrag>,
): void {
  for (const m of nachrichten) {
    const absenderId = Number(m.senderId)
    if (!Number.isInteger(absenderId) || absenderId <= 0) continue
    for (const anhang of [
      m.imageAttachment,
      m.audioAttachment,
      m.fileAttachment,
      m.videoNoteAttachment,
    ]) {
      const zeiger = zeigerVon(anhang)
      if (!zeiger || ziel.has(zeiger.mediaId)) continue
      ziel.set(zeiger.mediaId, {
        zeiger,
        bindung: { absenderId, blindMailboxId: mailbox },
        name: anhang?.name ?? null,
      })
    }
  }
}

/**
 * Die Anhänge eines Verlaufs an ein freigegebenes Gerät.
 *
 * Grenzen: 12 MB je Datei, 64 MB je Lauf. Was größer ist oder hier fehlt,
 * wird als fehlend gemeldet; die Übergabe gilt dann nicht als vollständig.
 */
export async function sendeMedienAnGeraet(
  ziel: E2eeGeraetItem,
  auftraege: readonly MedienAuftrag[],
  optionen: { beiTeilfehler?: () => void } = {},
): Promise<{ gesendet: number; fehlend: number }> {
  const konto = angemeldetesKonto()
  if (!konto || !ziel?.device_id || !ziel.public_key || auftraege.length === 0) {
    return { gesendet: 0, fehlend: 0 }
  }
  const meins = await eigenesGeraet()
  const geraeteMailbox = await deriveUserDeviceMailboxId(konto)
  let gesendet = 0
  let fehlend = 0
  let gesamt = 0

  for (const auftrag of auftraege) {
    const { mediaId } = auftrag.zeiger
    if (mediumGesendet(konto, ziel.device_id, mediaId)) continue
    let daten = await leseMedium(mediaId).catch(() => null)
    if (!daten) {
      // Ein anderes Gerät dieses Kontos kann die Datei haben; wenn nicht,
      // holt sie dieser eine Versuch noch vom Server, solange er sie hält.
      await sichereMedium(auftrag.zeiger, auftrag.bindung, auftrag.name).catch(() => false)
      daten = await leseMedium(mediaId).catch(() => null)
    }
    if (!daten || daten.length > MAX_MEDIA_ZEICHEN || gesamt + daten.length > MAX_MEDIA_GESAMT) {
      fehlend += 1
      optionen.beiTeilfehler?.()
      continue
    }

    const transferId = (
      await sha256Hex(utf8ToBytes(`msm:history-media:${meins.kennung}:${mediaId}`))
    ).slice(0, 40)
    const sha256 = await sha256Hex(utf8ToBytes(daten))
    const teile = Math.max(1, Math.ceil(daten.length / MEDIEN_TEIL_ZEICHEN))
    let vollstaendig = true
    for (let teil = 0; teil < teile; teil += 1) {
      try {
        const paket = JSON.stringify({
          v: 1,
          transferId,
          mediaId,
          name: auftrag.name,
          teil,
          teile,
          sha256,
          groesse: daten.length,
          daten: daten.slice(teil * MEDIEN_TEIL_ZEICHEN, (teil + 1) * MEDIEN_TEIL_ZEICHEN),
          ablauf: Date.now() + FENSTER_MS,
        })
        const innen = await encryptE2eeHybrid(paket, ziel.public_key)
        const inhalt = await signiereNutzlast(geraeteMailbox, konto, {
          type: VERLAUF_MEDIA_TYP,
          v: 1,
          ziel: ziel.device_id,
          geraet: meins.kennung,
          paket: innen,
        })
        const huelle = await encryptE2eeHybrid(
          JSON.stringify(inhalt),
          ziel.public_key,
          meins.paar.publicKeyJwk,
        )
        if (huelle.length > MAX_UMSCHLAG_ZEICHEN) throw new Error('zu gross')
        await relayE2eeEnvelope({
          blind_mailbox_id: geraeteMailbox,
          ciphertext_envelope: huelle,
          client_uuid: `media-${endung(ziel.device_id)}-${transferId.slice(0, 10)}-${teil}`,
          is_control: true,
          control_type: VERLAUF_MEDIA_TYP,
        })
        gesendet += 1
      } catch {
        vollstaendig = false
        break
      }
    }
    gesamt += daten.length
    if (!vollstaendig) {
      fehlend += 1
      optionen.beiTeilfehler?.()
    }
  }
  return { gesendet, fehlend }
}

/** Meldet dem Quellgerät, dass ein Anhang vollständig angekommen ist. */
async function sendeMedienAck(
  quellGeraet: string,
  mediaId: string,
  transferId: string,
): Promise<void> {
  const konto = angemeldetesKonto()
  if (!konto || !kennungInOrdnung(quellGeraet) || !kennungInOrdnung(mediaId)) return
  const meins = await eigenesGeraet()
  if (meins.kennung === quellGeraet) return
  const ziel = (await geraeteVon(konto)).find((g) => g.device_id === quellGeraet)
  if (!ziel?.public_key) return
  const geraeteMailbox = await deriveUserDeviceMailboxId(konto)
  const inhalt = await signiereNutzlast(geraeteMailbox, konto, {
    type: VERLAUF_MEDIA_ACK_TYP,
    v: 1,
    ziel: quellGeraet,
    geraet: meins.kennung,
    mediaId,
    transferId,
  })
  const huelle = await encryptE2eeHybrid(
    JSON.stringify(inhalt),
    ziel.public_key,
    meins.paar.publicKeyJwk,
  )
  await relayE2eeEnvelope({
    blind_mailbox_id: geraeteMailbox,
    ciphertext_envelope: huelle,
    client_uuid: `mediaack-${endung(quellGeraet)}-${transferId.slice(0, 10)}`,
    is_control: true,
    control_type: VERLAUF_MEDIA_ACK_TYP,
  })
}

/**
 * Ein Stück eines Anhangs annehmen — und, wenn es das letzte war, den
 * Anhang übernehmen.
 *
 * Ein Teil allein beweist nichts: erst mit allen Teilen, der richtigen Länge
 * und der Prüfsumme wird daraus ein Eintrag in der Medienablage. Stimmt etwas
 * nicht, fallen die Teile weg und der Absender schickt sie neu.
 */
export async function nimmtMedienTeil(
  roh: Record<string, unknown>,
  vonGeraet: string,
  entsiegle: (umschlag: string) => Promise<string>,
): Promise<number> {
  let paket: Record<string, unknown>
  try {
    const geparst = JSON.parse(await entsiegle(String(roh.paket || '')))
    if (!geparst || typeof geparst !== 'object') return 0
    paket = geparst as Record<string, unknown>
  } catch {
    return 0
  }
  if (Number(paket.v) !== 1) return 0
  const transferId = paket.transferId
  const mediaId = paket.mediaId
  if (!kennungInOrdnung(transferId) || !kennungInOrdnung(mediaId)) return 0
  const teile = Number(paket.teile)
  const teil = Number(paket.teil)
  const groesse = Number(paket.groesse)
  const ablauf = Number(paket.ablauf)
  if (!Number.isInteger(teile) || teile < 1 || teile > 4096) return 0
  if (!Number.isInteger(teil) || teil < 0 || teil >= teile) return 0
  if (!Number.isInteger(groesse) || groesse < 1 || groesse > MAX_MEDIA_ZEICHEN) return 0
  if (!Number.isFinite(ablauf) || ablauf < Date.now()) return 0
  if (typeof paket.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(paket.sha256)) return 0
  if (typeof paket.daten !== 'string' || paket.daten.length > MEDIEN_TEIL_ZEICHEN) return 0
  const name = typeof paket.name === 'string' ? paket.name.slice(0, 200) : null

  /*
   * Ein geloeschter Anhang kommt nicht zurueck.
   *
   * Das Quellgeraet kann offline gewesen sein, als die Nachricht geloescht
   * wurde, und schickt seinen Verlauf spaeter. Ohne diese Pruefung legte der
   * Transfer die Datei wieder in `medien` — geloescht waere sie nur auf dem
   * Geraet, das den Befehl gesehen hat. Geprueft wird vor dem Ablegen und nach
   * jedem `await`: zwischen zweien kann die Loeschung eintreffen.
   */
  if (await istMediumGeloescht(mediaId)) {
    await loescheMedienTeile(transferId).catch(() => {})
    return 1
  }

  await legeMedienTeilAb({
    transferId,
    mediaId,
    name,
    teil,
    teile,
    sha256: paket.sha256,
    groesse,
    daten: paket.daten,
  })

  const vorhanden = await holeMedienTeile(transferId)
  if (await istMediumGeloescht(mediaId)) {
    await loescheMedienTeile(transferId).catch(() => {})
    return 1
  }
  if (
    new Set(vorhanden.map((t) => t.teil)).size < teile ||
    !vorhanden.every(
      (t) => t.mediaId === mediaId && t.sha256 === paket.sha256 && t.groesse === groesse,
    )
  ) {
    return 0
  }
  const daten = vorhanden.map((t) => t.daten).join('')
  if (daten.length !== groesse) {
    await loescheMedienTeile(transferId)
    return 0
  }
  if ((await sha256Hex(utf8ToBytes(daten))) !== paket.sha256) {
    await loescheMedienTeile(transferId)
    return 0
  }
  if (await istMediumGeloescht(mediaId)) {
    await loescheMedienTeile(transferId).catch(() => {})
    return 1
  }
  // `false` heisst: der Grabstein ist inzwischen da. Dann gibt es kein ACK —
  // der Anhang ist hier nicht angekommen, er ist geloescht.
  const abgelegt = await speichereMedium(mediaId, daten, name)
  await loescheMedienTeile(transferId)
  if (!abgelegt) return 1
  await sendeMedienAck(vonGeraet, mediaId, transferId).catch(() => {})
  return 1
}
