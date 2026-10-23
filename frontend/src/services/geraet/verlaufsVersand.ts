/**
 * Ein Verlaufspaket an ein eigenes Gerät: innen verschlüsseln, außen
 * unterschreiben, in die Geräte-Mailbox legen.
 *
 * Textteile, Anhangsteile und Quittungen haben dieselbe Hülle. Bis 10/2026
 * stand dieser Ablauf dreimal nebeneinander (`verlaufsAbgleich`,
 * `verlaufsMedien`, `sendeMedienAck`); jede Kopie hätte die Grenze, die
 * Reihenfolge von Unterschrift und Versiegelung oder die Bindung an die
 * Mailbox anders treffen können. Er steht jetzt an einer Stelle.
 *
 * Was hier **nicht** passiert: Die Nutzlast nennt ihr Ziel und ihr Gerät
 * selbst; geprüft wird beides beim Empfänger (`verlaufeAbgleich`), nicht beim
 * Senden. Ein Absender, der sich irrt, kostet einen fehlgeschlagenen
 * Öffnungsversuch — kein anderer Weg darf daraus einen zweiten Zugang machen.
 */

import { relayE2eeEnvelope, type E2eeGeraetItem } from '@/api/social'
import { encryptE2eeHybrid } from '../e2eeCrypto'
import { signiereNutzlast } from '../nutzlastSignatur'
import type { EigenesGeraet } from './geraeteAblage'

/** Das Relais nimmt 1 MiB Zeichen je Umschlag. */
export const MAX_UMSCHLAG_ZEICHEN = 1_000_000

/**
 * Ein Übertragungsfenster. Ältere Manifeste und Teile werden verworfen; danach
 * beginnt ein neuer, ausdrücklicher Transfer. Das Fenster ersetzt nicht die
 * 30-Tage-Frist der Nachrichtenwarteschlange.
 */
export const FENSTER_MS = 24 * 60 * 60 * 1000

/** Ein kurzer, stabiler Namensrest einer Gerätekennung. */
export function endung(geraetId: string): string {
  const sauber = (geraetId || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(-16)
  return sauber || 'geraet'
}

/** Kennungen, die in einem Signaturfeld oder einer Paketkennung stehen dürfen. */
export function kennungInOrdnung(wert: unknown): wert is string {
  return typeof wert === 'string' && /^[A-Za-z0-9._:-]{1,120}$/.test(wert)
}

/** Was ein Paket an ein Zielgerät ausmacht. */
export interface PaketAuftrag {
  konto: number
  /** Das Gerät, das sendet. Sein Signaturschlüssel beglaubigt die Nutzlast. */
  meins: EigenesGeraet
  ziel: E2eeGeraetItem
  /** Die Geräte-Mailbox des Kontos; sie ist die Bindung der Unterschrift. */
  mailbox: string
  typ: string
  /** Bleibt über Wiederholungen gleich, damit das Relais nicht doppelt ablegt. */
  clientUuid: string
  /** Felder der äußeren, signierten Nutzlast. */
  felder?: Record<string, unknown>
  /** Klartext der inneren Nutzlast; fehlt er, trägt das Paket nur `felder`. */
  innereDaten?: string
}

/**
 * Versiegelt, unterschreibt und legt ein Paket ab.
 *
 * Gibt die äußere Hülle zurück. Ist sie größer als das Relais annimmt, wirft
 * die Funktion; der Aufrufer entscheidet, ob er den Teil kleiner schneidet
 * (Text) oder als fehlend meldet (Anhang).
 */
export async function sendeSigniertesPaket(auftrag: PaketAuftrag): Promise<string> {
  const { konto, meins, ziel, mailbox, typ, clientUuid, felder, innereDaten } = auftrag
  if (!ziel?.device_id || !ziel.public_key) throw new Error('Zielgerät ohne Schlüssel')
  if (!kennungInOrdnung(typ) || !clientUuid) throw new Error('Paket ohne Kennung')

  const innen =
    innereDaten === undefined ? undefined : await encryptE2eeHybrid(innereDaten, ziel.public_key)
  const inhalt = await signiereNutzlast(mailbox, konto, {
    type: typ,
    v: 1,
    ziel: ziel.device_id,
    geraet: meins.kennung,
    ...(felder ?? {}),
    ...(innen === undefined ? {} : { paket: innen }),
  })
  const huelle = await encryptE2eeHybrid(
    JSON.stringify(inhalt),
    ziel.public_key,
    meins.paar.publicKeyJwk,
  )
  if (huelle.length > MAX_UMSCHLAG_ZEICHEN) throw new Error('Paket zu groß')
  await relayE2eeEnvelope({
    blind_mailbox_id: mailbox,
    ciphertext_envelope: huelle,
    client_uuid: clientUuid,
    is_control: true,
    control_type: typ,
  })
  return huelle
}
