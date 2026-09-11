import { describe, it, expect } from 'vitest'
import {
  generateLocalE2eeKeyPair,
  encryptE2eeHybrid,
  decryptE2eeHybrid,
  encryptE2eeAttachmentBlob,
  decryptE2eeAttachmentBlob,
  type AttachmentCryptoContext,
} from '@/services/e2eeCrypto'

describe('End-to-end Attachment Flow Simulation', () => {
  it('tests full lifecycle of camera photo attachment', async () => {
    // 1. Setup Alice (sender) and Bob (recipient)
    const aliceKeys = await generateLocalE2eeKeyPair()
    const bobKeys = await generateLocalE2eeKeyPair()

    // 2. Alice takes a photo with camera
    const photoDataUrl = 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA='
    const photoName = 'kamera-aufnahme.jpg'
    const blindMailboxId = 'test-mailbox-1234567890123456'

    // 3. Encrypt attachment blob (as in uploadEncryptedChatAttachment)
    const cryptoContext: AttachmentCryptoContext = {
      userAId: 1, // Alice
      userBId: 2, // Bob
    }
    const encryptedBlob = await encryptE2eeAttachmentBlob(photoDataUrl, cryptoContext)
    expect(encryptedBlob.startsWith('sv-blob-v1:')).toBe(true)

    // Suppose server returns mediaId: 'media-uuid-1'
    const uploadedMediaId = 'media-uuid-1'

    // What happens in handleSendMessage:
    // Notice what handleSendMessage currently does:
    const finalImg = {
      dataUrl: photoDataUrl,
      name: photoName,
      mediaId: uploadedMediaId,
    }

    const payloadObj: Record<string, unknown> = {
      client_uuid: 'uuid-12345',
      sender_id: 1,
      sender_name: 'Alice',
      text: '', // No text entered!
      timestamp: new Date().toISOString(),
      image_attachment: finalImg,
    }

    const payload = JSON.stringify(payloadObj)

    // Alice encrypts for Bob
    const ciphertext = await encryptE2eeHybrid(payload, bobKeys.publicKeyJwk, aliceKeys.publicKeyJwk)

    // 4. Bob receives envelope from mailbox
    const bobDecryptedPlain = await decryptE2eeHybrid(ciphertext, bobKeys.privateKeyJwk)
    const bobParsed = JSON.parse(bobDecryptedPlain)
    expect(bobParsed.image_attachment).toBeDefined()
    expect(bobParsed.image_attachment.mediaId).toBe('media-uuid-1')

    // Alice (sender) decrypts own envelope from mailbox
    const aliceDecryptedPlain = await decryptE2eeHybrid(ciphertext, aliceKeys.privateKeyJwk)
    const aliceParsed = JSON.parse(aliceDecryptedPlain)
    expect(aliceParsed.image_attachment).toBeDefined()
    expect(aliceParsed.image_attachment.mediaId).toBe('media-uuid-1')
  })
})
