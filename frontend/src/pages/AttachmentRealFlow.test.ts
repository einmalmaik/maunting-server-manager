// @vitest-environment node
/**
 * Der ganze Weg eines Anhangs, einmal durchgespielt: Kamerabild verschlüsseln,
 * Zeiger in den Nachrichten-Payload legen, Payload versiegeln, und auf der
 * anderen Seite alles wieder aufmachen.
 *
 * Der Test hieß früher genauso und prüfte `encryptE2eeAttachmentBlob`. Dessen
 * Schlüssel ergab sich aus den beiden Benutzerkennungen — die stehen in der
 * Datenbank, also konnte das Backend jeden Anhang öffnen. Seit 09/2026 bekommt
 * jeder Anhang einen zufälligen Paketschlüssel, und der reist im
 * verschlüsselten Nachrichten-Payload.
 *
 * Node-Umgebung, nicht jsdom: DIS prüft Eingaben mit `instanceof Uint8Array`.
 */

import { describe, expect, it } from 'vitest'

import {
  decryptE2eeHybrid,
  encryptE2eeHybrid,
  generateLocalE2eeKeyPair,
} from '@/services/e2eeCrypto'
import {
  ANHANG_PREFIX,
  entschluesselePaket,
  neueFileId,
  verschluesselePaket,
} from '@/services/medienKrypto'

const ALICE = 1
const BOB = 2
const MAILBOX = 'test-mailbox-1234567890123456'

describe('Anhang von Ende zu Ende', () => {
  it('trägt ein Kamerabild von Alice zu Bob', async () => {
    const aliceKeys = await generateLocalE2eeKeyPair()
    const bobKeys = await generateLocalE2eeKeyPair()

    const foto =
      'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA='

    // 1. Alice verschlüsselt den Anhang gegen einen frischen Paketschlüssel.
    const fileId = neueFileId()
    const { blob, paketSchluessel } = await verschluesselePaket(
      foto,
      { absenderId: ALICE, blindMailboxId: MAILBOX, fileId },
      { name: 'kamera-aufnahme.jpg', mimeType: 'image/jpeg' },
    )
    expect(blob.startsWith(ANHANG_PREFIX)).toBe(true)
    // Der Server bekommt genau das hier — und darin steht nichts vom Bild.
    expect(blob).not.toContain('kamera-aufnahme')

    // 2. Der Zeiger auf den hochgeladenen Blob wandert in den Payload.
    const mediaId = 'media-uuid-1'
    const payload = JSON.stringify({
      client_uuid: 'uuid-12345',
      sender_id: ALICE,
      sender_name: 'Alice',
      text: '',
      timestamp: new Date().toISOString(),
      image_attachment: { mediaId, paketSchluessel, fileId, name: 'kamera-aufnahme.jpg' },
    })

    // 3. Der Payload geht versiegelt in die Mailbox.
    const umschlag = await encryptE2eeHybrid(payload, bobKeys.publicKeyJwk, aliceKeys.publicKeyJwk)
    expect(umschlag).not.toContain(paketSchluessel)

    // 4. Bob macht auf: erst den Umschlag, dann den Anhang.
    const beiBob = JSON.parse(await decryptE2eeHybrid(umschlag, bobKeys.privateKeyJwk))
    expect(beiBob.image_attachment.mediaId).toBe(mediaId)

    const gelesen = await entschluesselePaket(blob, beiBob.image_attachment.paketSchluessel, {
      absenderId: Number(beiBob.sender_id),
      blindMailboxId: MAILBOX,
      fileId: beiBob.image_attachment.fileId,
    })
    expect(gelesen).toBe(foto)

    // 5. Alice liest ihre eigene Kopie genauso.
    const beiAlice = JSON.parse(await decryptE2eeHybrid(umschlag, aliceKeys.privateKeyJwk))
    expect(
      await entschluesselePaket(blob, beiAlice.image_attachment.paketSchluessel, {
        absenderId: ALICE,
        blindMailboxId: MAILBOX,
        fileId: beiAlice.image_attachment.fileId,
      }),
    ).toBe(foto)
  })

  it('nützt dem Server nichts, dass er Absender und Mailbox kennt', async () => {
    // Genau das war die Lücke des alten Verfahrens: wer die beiden
    // Benutzerkennungen kannte, hatte den Schlüssel. Heute kennt der Server sie
    // weiterhin — und kommt trotzdem nicht hinein.
    const fileId = neueFileId()
    const geheim = 'data:text/plain;base64,' + Buffer.from('nur für Bob').toString('base64')
    const { blob } = await verschluesselePaket(
      geheim,
      { absenderId: ALICE, blindMailboxId: MAILBOX, fileId },
      { name: 'notiz.txt', mimeType: 'text/plain' },
    )

    // Der Versuch mit allem, was in der Datenbank steht, aber ohne Paketschlüssel.
    const geraten = Buffer.alloc(32, 0).toString('base64')
    await expect(
      entschluesselePaket(blob, geraten, {
        absenderId: ALICE,
        blindMailboxId: MAILBOX,
        fileId,
      }),
    ).rejects.toThrow()
  })

  it('lässt einen Anhang nicht in ein fremdes Gespräch umhängen', async () => {
    const fileId = neueFileId()
    const inhalt = 'data:text/plain;base64,' + Buffer.from('vertraulich').toString('base64')
    const { blob, paketSchluessel } = await verschluesselePaket(
      inhalt,
      { absenderId: ALICE, blindMailboxId: MAILBOX, fileId },
      { name: 'notiz.txt', mimeType: 'text/plain' },
    )

    // Selbst mit dem richtigen Paketschlüssel: Mailbox und Absender stehen in
    // den gebundenen Daten jedes Stücks.
    await expect(
      entschluesselePaket(blob, paketSchluessel, {
        absenderId: BOB,
        blindMailboxId: MAILBOX,
        fileId,
      }),
    ).rejects.toThrow()
    await expect(
      entschluesselePaket(blob, paketSchluessel, {
        absenderId: ALICE,
        blindMailboxId: 'eine-ganz-andere-mailbox-0000',
        fileId,
      }),
    ).rejects.toThrow()
  })
})
