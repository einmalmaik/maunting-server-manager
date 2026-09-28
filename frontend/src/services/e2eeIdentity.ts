/**
 * Die E2EE-Identität, wie der Messenger sie sieht — jetzt aus dem Gerät.
 *
 * Diese Datei trug bis 09/2026 den Kontoschlüsselbund: ein RSA-Paar, dessen
 * privater Teil mit Argon2id aus einem abgetippten Wiederherstellungsschlüssel
 * verpackt beim Server lag. Das ist weg, und zwar ersatzlos — mit dem Double
 * Ratchet ist ein über Geräte geteilter privater Schlüssel nicht bloß unnötig,
 * sondern schädlich: der Ratchet vernichtet beim Entschlüsseln den Schlüssel,
 * der die Nachricht geöffnet hat, und zwei Geräte am selben Faden laufen
 * unweigerlich auseinander.
 *
 * Geblieben ist eine schmale Fassade über `e2eeGeraet.ts`, damit der Messenger
 * seine bisherigen Namen behält. Neue Aufrufer greifen direkt auf `e2eeGeraet`
 * zu; diese Datei verschwindet, sobald der Messenger auf den Ratchet-Pfad
 * umgestellt ist.
 *
 * Der Zustand hat nur noch zwei Werte: `loading`, solange das Gerät seinen
 * Schlüssel noch anlegt oder veröffentlicht, und danach `ready`. `locked` gab
 * es, weil ein Gerät auf eine Eingabe warten musste. Darauf wartet nichts mehr.
 */

import {
  eigenesGeraet,
  geraetVeroeffentlichen,
  verlangeGeraeteVon,
  vergessenGeraete,
  E2eeKeinGeraetError,
} from './e2eeGeraet'
import type { LocalE2eeKeyPair } from './e2eeCrypto'

export type IdentityState = 'loading' | 'ready'

export interface E2eeIdentity {
  state: IdentityState
  /** Das Paar dieses Geräts. Nur im Zustand `ready` gesetzt. */
  sendPair: LocalE2eeKeyPair | null
  /** Private Schlüssel zum Lesen — genau einer, der dieses Geräts. */
  decryptionKeys: string[]
}

/** Startwert für Komponenten, bevor `resolveIdentity` geantwortet hat. */
export const IDENTITY_LOADING: E2eeIdentity = Object.freeze({
  state: 'loading' as const,
  sendPair: null,
  decryptionKeys: [] as string[],
})

/** Der Empfänger hat noch kein Gerät angemeldet. Siehe `E2eeKeinGeraetError`. */
export const E2eeRecipientKeyMissingError = E2eeKeinGeraetError

/**
 * Legt die Identität dieses Geräts an, falls nötig, und meldet sie beim Konto.
 *
 * Erzeugt im Gegensatz zu früher bedenkenlos: ein Geräteschlüssel überschreibt
 * nichts. Genau das war beim Kontoschlüssel der Fehler, gegen den die alte
 * Fassung mit `locked` abgesichert war.
 */
export async function resolveIdentity(userId: number): Promise<E2eeIdentity> {
  if (!userId) return IDENTITY_LOADING
  const geraet = await eigenesGeraet()
  // Fehlschlag beim Veröffentlichen ist nicht fatal: lesen kann dieses Gerät
  // trotzdem. Es bleibt nur so lange unerreichbar, bis die Meldung durchgeht.
  await geraetVeroeffentlichen().catch(() => {})
  return {
    state: 'ready',
    sendPair: geraet.paar,
    decryptionKeys: [geraet.paar.privateKeyJwk],
  }
}

/**
 * Der Schlüssel eines Empfängers für den Sendepfad.
 *
 * Liefert den des zuletzt aktiven Geräts. Der eigentliche Sendepfad fächert
 * ab Scheibe 3 über `verlangeGeraeteVon` an **alle** Geräte auf; diese
 * Abkürzung trägt nur die Aufrufer, die noch einen einzelnen Schlüssel
 * erwarten.
 */
export async function requireRecipientPublicKey(userId: number): Promise<string> {
  const geraete = await verlangeGeraeteVon(userId)
  return geraete[0].public_key
}

export function forgetRecipientPublicKey(userId: number): void {
  vergessenGeraete(userId)
}
