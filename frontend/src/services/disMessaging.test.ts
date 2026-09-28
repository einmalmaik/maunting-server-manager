// @vitest-environment node
/**
 * Wächter über den eingecheckten DIS-Abzug unter `frontend/packages/dis/`.
 *
 * **Node-Umgebung, nicht jsdom.** DIS prüft Eingaben mit `instanceof Uint8Array`.
 * Unter jsdom liefert `TextEncoder` ein `Uint8Array` aus der Node-Realm, während
 * der Test gegen jsdoms eigenes `Uint8Array` prüft — dieselben Bytes, andere
 * Konstruktorkette, und die Prüfung schlägt fehl. Im Browser und im Tauri-WebView
 * gibt es nur eine Realm; das Problem existiert ausschließlich im Test.
 *
 * Das Paket liegt nicht als Quelltext im Repo, sondern als Abzug des
 * veröffentlichten Artefakts — und seine `package.json` wird beim Vendern
 * bewusst getrimmt (ohne `dependencies`, `scripts`, `devDependencies`, weil
 * `hash-wasm` und `otpauth` direkt in `frontend/package.json` stehen). Ein
 * unvollständiger oder falsch getrimmter Abzug fällt sonst erst im Betrieb auf.
 *
 * Geprüft wird deshalb nicht die Krypto von DIS — die hat ihre eigenen Tests —
 * sondern dass `@msdis/shield/messaging` auflösbar ist und ein Hin und Her
 * trägt.
 */

import { describe, expect, it } from 'vitest'
import {
  generateRatchetKeyPair,
  initSenderState,
  initReceiverState,
  encryptMessage,
  decryptMessage,
  serializeRatchetState,
  RATCHET_STATE_V1_PREFIX,
} from '@msdis/shield/messaging'
import { randomBytes } from '@msdis/shield/random'

const text = new TextEncoder()
const zurueck = new TextDecoder()

describe('vendorter DIS-Abzug: @msdis/shield/messaging', () => {
  it('trägt ein Hin und Her und lässt sich persistieren', async () => {
    const paar = await generateRatchetKeyPair()
    const geheimnis = randomBytes(32)

    // initSenderState nullt sein `sharedSecret` — beide Seiten brauchen daher
    // je eine eigene Kopie derselben 32 Bytes.
    let alice = await initSenderState({
      sharedSecret: geheimnis.slice(),
      remotePublicKey: paar.publicKey,
    })
    let bob = await initReceiverState({
      sharedSecret: geheimnis.slice(),
      dhKeyPair: paar,
    })

    const hin = await encryptMessage(alice, text.encode('Servus Bob'))
    alice = hin.nextState
    const gelesen = await decryptMessage(bob, hin.message)
    bob = gelesen.nextState
    expect(zurueck.decode(gelesen.plaintext)).toBe('Servus Bob')

    // Erst nach der ersten empfangenen Nachricht hat Bob eine Sendekette.
    const zurueckhin = await encryptMessage(bob, text.encode('Servus Alice'))
    const gelesen2 = await decryptMessage(alice, zurueckhin.message)
    expect(zurueck.decode(gelesen2.plaintext)).toBe('Servus Alice')

    expect(serializeRatchetState(alice).startsWith(RATCHET_STATE_V1_PREFIX)).toBe(true)
  })
})
