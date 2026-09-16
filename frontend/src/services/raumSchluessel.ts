/**
 * Der Raumschlüssel eines Anrufs: erzeugen, verpacken, zustellen, auspacken.
 *
 * Der Medienserver leitet alle Ströme weiter und darf sie trotzdem nicht
 * mithören. Dafür braucht jeder Raum einen Schlüssel, den ausschließlich die
 * Teilnehmer kennen.
 *
 * **Warum nicht `deriveGroupChannelKey` / `deriveDirectChannelKey`.** Beide
 * leiten ohne Passphrase allein aus den Benutzerkennungen ab
 * (`sha256("msm:dm:key:<a>:<b>")`). Das Backend kennt diese Kennungen und kann
 * denselben Schlüssel bilden — `e2eeCrypto.ts` sagt das an beiden Stellen
 * selbst. Für Medien wäre das eine Verschlüsselung, die genau den nicht
 * aussperrt, den sie aussperren soll.
 *
 * Stattdessen: 32 Zufallsbytes je Raum, für jeden Teilnehmer einzeln gegen
 * dessen veröffentlichten Schlüssel verpackt (`encryptE2eeHybrid`, RSA-OAEP)
 * und über `POST /social/calls/{raum}/key` zugestellt. Das Panel reicht einen
 * Umschlag durch, den es nicht öffnen kann, und speichert ihn nicht.
 */

import { sendeRaumSchluessel } from '@/api/calls'
import { decryptE2eeHybridWithKeyring, encryptE2eeHybrid } from '@/services/e2eeCrypto'
import { requireRecipientPublicKey } from '@/services/e2eeIdentity'

const SCHLUESSEL_BYTES = 32

export function erzeugeRaumSchluessel(): Uint8Array {
  const bytes = new Uint8Array(SCHLUESSEL_BYTES)
  crypto.getRandomValues(bytes)
  return bytes
}

export function schluesselNachBase64(schluessel: Uint8Array): string {
  let roh = ''
  for (const byte of schluessel) roh += String.fromCharCode(byte)
  return btoa(roh)
}

export function schluesselAusBase64(kodiert: string): Uint8Array {
  const roh = atob(kodiert)
  const bytes = new Uint8Array(roh.length)
  for (let i = 0; i < roh.length; i += 1) bytes[i] = roh.charCodeAt(i)
  return bytes
}

/** LiveKits KeyProvider erwartet einen ArrayBuffer (dann HKDF statt PBKDF2). */
export function alsArrayBuffer(schluessel: Uint8Array): ArrayBuffer {
  return schluessel.slice().buffer
}

/**
 * Stellt den Raumschlüssel an einen Teilnehmer zu.
 *
 * Scheitert still, wenn der Empfänger keinen veröffentlichten Schlüssel hat.
 * Für den Anruf heißt das: die Gegenstelle hört nichts, und das Overlay meldet
 * das. Eine Ersatzverschlüsselung gäbe es hier nicht — sie wäre keine.
 */
export async function verteileAn(
  raum: string,
  schluessel: Uint8Array,
  empfaengerId: number,
  eigenerOeffentlicherSchluessel: string,
): Promise<boolean> {
  try {
    const empfaengerSchluessel = await requireRecipientPublicKey(empfaengerId)
    const umschlag = await encryptE2eeHybrid(
      schluesselNachBase64(schluessel),
      empfaengerSchluessel,
      eigenerOeffentlicherSchluessel,
    )
    await sendeRaumSchluessel(raum, empfaengerId, umschlag)
    return true
  } catch {
    return false
  }
}

/** Verteilt an mehrere Empfänger. Gibt zurück, wen es nicht erreicht hat. */
export async function verteileAnAlle(
  raum: string,
  schluessel: Uint8Array,
  empfaengerIds: readonly number[],
  eigenerOeffentlicherSchluessel: string,
): Promise<number[]> {
  const ergebnisse = await Promise.all(
    empfaengerIds.map(async (id) => ({
      id,
      ok: await verteileAn(raum, schluessel, id, eigenerOeffentlicherSchluessel),
    })),
  )
  return ergebnisse.filter((e) => !e.ok).map((e) => e.id)
}

/** Öffnet einen zugestellten Umschlag mit dem eigenen Schlüsselbund. */
export async function entpacke(
  ciphertext: string,
  entschluesselungsSchluessel: readonly string[],
): Promise<Uint8Array> {
  const base64 = await decryptE2eeHybridWithKeyring(ciphertext, entschluesselungsSchluessel)
  const bytes = schluesselAusBase64(base64.trim())
  if (bytes.length !== SCHLUESSEL_BYTES) {
    throw new Error('Raumschlüssel hat die falsche Länge')
  }
  return bytes
}

/**
 * Soll **ich** einem Nachzügler den Schlüssel schicken?
 *
 * Alle bereits Anwesenden haben ihn, aber es soll nicht jeder gleichzeitig
 * senden. Die Regel ist deshalb rein lokal und für alle dieselbe: die kleinste
 * Benutzerkennung unter den Anwesenden schickt. Jeder Client sieht dieselbe
 * Teilnehmerliste und kommt zum selben Ergebnis, ohne Absprache und ohne
 * Server. Weichen zwei Clients kurz ab, kommt der Schlüssel doppelt an — was
 * nichts kostet, weil es derselbe ist.
 */
export function istSchluesselhalter(
  eigeneId: number,
  anwesendeIds: readonly number[],
  nachzueglerId: number,
): boolean {
  const andere = anwesendeIds.filter((id) => id !== nachzueglerId)
  const kandidaten = andere.includes(eigeneId) ? andere : [...andere, eigeneId]
  return Math.min(...kandidaten) === eigeneId
}
