/**
 * Das Format eines Datensatzes im Posteingang des Tresors, für beide Seiten:
 * wer ablegt (Telefon in `KameraAuftrag.kt`, Browser über `eingangVerpacken`)
 * und wer übernimmt (`tresorEingang.ts`).
 *
 * Der Inhalt ist hybrid verschlüsselt (ML-KEM-768 + RSA-4096) mit dem
 * öffentlichen Schlüssel des Tresors, gebunden an Bucket und Kennung, und auf
 * 4 KiB gepolstert, damit die Länge nichts verrät. Unterschrieben wird alles
 * am Umschlag außer der Unterschrift, mit dem Schlüssel des Geräts.
 */

import { bytesToBase64, utf8ToBytes } from '@msdis/shield/core'
import { hybridEncrypt } from '@msdis/shield/post-quantum'

export const FORMAT = 1
const DOMAENE = 'msm-tresor-eingang-v1'
const POLSTER = 4096

/** Was ein Gerät bei gesperrtem Tresor vom Schlüsselpaar kennt. */
export interface EingangOeffentlich {
  id: string
  pqPublicKey: string
  rsaPublicKey: string
}

/** So liegt ein Datensatz beim Server. Nur `daten` ist verschlüsselt. */
export interface Umschlag {
  v: typeof FORMAT
  /** Kennung des Schlüsselpaars. */
  schluessel: string
  /** Kennung des Geräts, das unterschrieben hat. */
  geraet: string
  daten: string
  /** r ‖ s in Base64. */
  signatur: string
}

export function aad(bucket: string, eingangId: string): string {
  return `${DOMAENE}:${bucket}:${eingangId}`
}

/** Was unterschrieben wird: alles am Umschlag außer der Unterschrift, gebunden an Bucket und Kennung. */
export function zuUnterschreiben(bucket: string, eingangId: string, u: Omit<Umschlag, 'signatur'>): Uint8Array {
  return utf8ToBytes([DOMAENE, String(u.v), bucket, eingangId, u.schluessel, u.geraet, u.daten].join('\n'))
}

export function istUmschlag(wert: unknown): wert is Umschlag {
  if (!wert || typeof wert !== 'object') return false
  const u = wert as Record<string, unknown>
  return (
    u.v === FORMAT &&
    typeof u.schluessel === 'string' &&
    typeof u.geraet === 'string' &&
    typeof u.daten === 'string' &&
    typeof u.signatur === 'string'
  )
}

/** Verpackt einen Inhalt wie `KameraAuftrag.kt`. Braucht keinen geheimen Schlüssel des Tresors. */
export async function eingangVerpacken(
  inhalt: object,
  bucket: string,
  eingangId: string,
  oeffentlich: EingangOeffentlich,
  geraet: string,
  unterschreiben: (daten: Uint8Array) => Promise<Uint8Array>,
): Promise<string> {
  const json = JSON.stringify(inhalt)
  const laenge = utf8ToBytes(json).length
  const gepolstert = json + ' '.repeat(Math.ceil((laenge + 1) / POLSTER) * POLSTER - laenge)
  const daten = await hybridEncrypt(gepolstert, oeffentlich.pqPublicKey, oeffentlich.rsaPublicKey, aad(bucket, eingangId))
  const ohne = { v: FORMAT, schluessel: oeffentlich.id, geraet, daten } as const
  const signatur = bytesToBase64(await unterschreiben(zuUnterschreiben(bucket, eingangId, ohne)))
  return JSON.stringify({ ...ohne, signatur })
}
