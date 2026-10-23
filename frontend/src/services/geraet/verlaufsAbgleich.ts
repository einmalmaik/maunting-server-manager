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
 * Der Text reist seit 10/2026 in einem eigenen Vertrag mit Manifest und
 * Empfangsquittung (`verlaufsText.ts`): `msm:history_sent` gilt erst nach
 * `history_done`, und ein verlorener Teil wird beim nächsten Lauf unter
 * derselben Kennung nachgeholt. Anhänge reisen als eigene, geprüfte Pakete aus
 * der lokalen Ablage mit (`verlaufsMedien.ts`) — der Server hält einen Blob nur
 * 30 Tage, der Verweis allein wäre nach Ablauf wertlos.
 *
 * Diese Datei ist der gemeinsame Eingang: sie prüft die äußere Hülle
 * (Zielgerät, Unterschrift eines eigenen Geräts, Bindung an die
 * Geräte-Mailbox) und verteilt danach an den zuständigen Handler. Kein
 * Handler prüft dieselbe Hülle ein zweites, anders Mal.
 */

import type { E2eeGeraetItem } from '@/api/social'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { deriveUserDeviceMailboxId } from '../e2eeCrypto'
import { pruefeNutzlast } from '../nutzlastSignatur'
import { eigenesGeraet } from './geraeteAblage'
import { geraeteVon, verzeichnisVon } from './geraeteVerzeichnis'
import {
  nimmtTextManifest,
  nimmtTextTeil,
  quittiereTextTeil,
  quittiereTextFertig,
  sendeTextVerlaufAnGeraet,
} from './verlaufsText'
import { merkeMediumGesendet, nimmtMedienTeil, sendeMedienAnGeraet } from './verlaufsMedien'
import { merkeVerlaufGesendet } from './verlaufsStand'
import {
  VERLAUF_DONE_TYP,
  VERLAUF_MANIFEST_TYP,
  VERLAUF_MEDIA_ACK_TYP,
  VERLAUF_MEDIA_TYP,
  VERLAUF_PART_ACK_TYP,
  VERLAUF_SYNC_TYP,
  istVerlaufsTyp,
} from './verlaufsTypen'
import { kennungInOrdnung } from './verlaufsVersand'

export { VERLAUF_SYNC_TYP, merkeVerlaufGesendet }

/**
 * Schickt den lokalen Verlauf an ein freigegebenes Gerät desselben Kontos.
 *
 * Gibt die Zahl der in diesem Lauf verschickten **Teile** zurück (Manifest
 * zählt nicht; Anhänge zählen mit). Wirft nicht bei einem einzelnen Paket:
 * eine zu große oder kaputte Mailbox hält die übrigen nicht auf. Der Aufrufer
 * entscheidet, ob er das Ergebnis meldet.
 */
export async function sendeVerlaufAnGeraet(
  ziel: E2eeGeraetItem,
  optionen: { beiTeilfehler?: () => void } = {},
): Promise<number> {
  const konto = angemeldetesKonto()
  if (!konto || !ziel?.device_id || !ziel.public_key) return 0
  const meins = await eigenesGeraet()
  if (meins.kennung === ziel.device_id) return 0

  // Der Textvertrag prüft selbst, ob dieses Ziel schon quittiert hat. Auch
  // danach wird der Bestand gelesen: ein Anhang kann noch offen sein, wenn die
  // Schlussquittung für den Text früher kam als seine eigene.
  const text = await sendeTextVerlaufAnGeraet(ziel, konto, meins, optionen)
  let gesendet = text.gesendet
  if (text.medien.size > 0) {
    const ergebnis = await sendeMedienAnGeraet(ziel, [...text.medien.values()], optionen).catch(
      () => ({ gesendet: 0, fehlend: text.medien.size }),
    )
    gesendet += ergebnis.gesendet
    if (ergebnis.fehlend > 0) optionen.beiTeilfehler?.()
  }
  return gesendet
}

/**
 * Holt den Abgleich für alle freigegebenen Geschwister nach.
 *
 * Wird beim Öffnen des Messengers gerufen. Der Fall dahinter: ein Gerät wurde
 * freigegeben, bevor es diese Übergabe gab — dann steht es in der Liste, hat
 * aber nie einen Verlauf bekommen. Der Merker `msm:history_sent` fällt erst
 * nach `history_done`; solange er fehlt, geht der offene Rest erneut hinaus.
 * Ist der Text fertig, bleibt nur noch ein offener Anhang übrig — auch dafür
 * wird der Bestand einmal gelesen.
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
  // **Frisch gefragt.** `geraeteVon` darf zehn Minuten aus dem
  // Zwischenspeicher antworten; ein gerade freigegebenes Gerät fehlt darin
  // genau in dem Augenblick, in dem es gebraucht wird — und bekommt seinen
  // Verlauf erst beim übernächsten Start.
  const geschwister = await verzeichnisVon(konto, { frisch: true }).catch(() => geraeteVon(konto))
  let gesendet = 0
  for (const geraet of geschwister) {
    if (geraet.device_id === meins.kennung) continue
    try {
      let teilfehler = false
      const pakete = await sendeVerlaufAnGeraet(geraet, {
        beiTeilfehler: () => {
          teilfehler = true
        },
      })
      if (pakete > 0 && !teilfehler) gesendet += 1
    } catch {
      // Beim nächsten Start erneut. Kein Grund, den Messenger zu behindern.
    }
  }
  return gesendet
}

/**
 * Nimmt ein Verlaufspaket aus der eigenen Geräte-Mailbox an.
 *
 * Drei Schranken, für alle Typen dieselben:
 * 1. Es ist an **dieses** Gerät adressiert (`ziel`).
 * 2. Es ist von einem Gerät des eigenen Kontos unterschrieben, und die
 *    Unterschrift deckt die Geräte-Mailbox als Bindung.
 * 3. Das innere Paket lässt sich mit dem eigenen Schlüsselbund öffnen.
 *
 * Erst danach entscheidet der Typ, was geschieht. Ein gefälschtes Paket kann
 * damit keinen Verlauf einschleusen; es kostet höchstens einen fehlgeschlagenen
 * Öffnungsversuch.
 */
export async function verarbeiteVerlaufsAbgleich(
  roh: Record<string, unknown>,
  eigeneId: number,
  entsiegle: (umschlag: string) => Promise<string>,
): Promise<number> {
  const typ = roh.type
  if (!istVerlaufsTyp(typ)) return 0
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

  // Ein Anhang ist beim Ziel angekommen und geprueft: erst jetzt gilt er beim
  // Absender als uebergeben.
  if (typ === VERLAUF_MEDIA_ACK_TYP) {
    const mediaId = roh.mediaId
    if (!kennungInOrdnung(mediaId)) return 0
    merkeMediumGesendet(Number(konto), vonGeraet, mediaId)
    return 1
  }

  if (typ === VERLAUF_MEDIA_TYP) {
    return await nimmtMedienTeil(roh, vonGeraet, entsiegle)
  }

  // Das Manifest vor den Teilen. Ein Teil ohne angenommenes Manifest wird
  // abgewiesen; die Quelle schickt es beim nächsten Lauf erneut.
  if (typ === VERLAUF_MANIFEST_TYP) {
    return await nimmtTextManifest(roh, vonGeraet, entsiegle)
  }

  // Die Quittungen der Gegenseite. Sie ändern nur den Plan der Quelle; die
  // äußere Prüfung oben ist dieselbe wie für jedes eingehende Paket.
  if (typ === VERLAUF_PART_ACK_TYP) {
    return await quittiereTextTeil(roh, vonGeraet)
  }
  if (typ === VERLAUF_DONE_TYP) {
    return await quittiereTextFertig(roh, vonGeraet)
  }

  if (typ === VERLAUF_SYNC_TYP) {
    return await nimmtTextTeil(roh, vonGeraet, entsiegle)
  }
  return 0
}
