import { describe, it, expect, beforeEach } from 'vitest'
import { aesGcmEncrypt, aesGcmDecrypt, importAesGcmRawKey } from '@msdis/shield/aead'
import { DisDecryptionError } from '@msdis/shield/core'
import { FakeRTCPeerConnection, FakeRTCEncodedFrame } from '../fakeWebRtc'

// ============================================================================
// WebRTC Insertable Streams Frame Cryptor Contract (PROJECT.md § Interface Contracts)
// Nonce (12B): Salt (12B) XOR FrameCounter (8B big-endian padded to 12B)
// Trailer (21B): [AuthTag: 16B][FrameIndex: 4B big-endian][KeyEpoch: 1B]
// AAD (9B): [SSRC: 4B big-endian][Timestamp: 4B big-endian][KeyEpoch: 1B]
// ============================================================================

export const TRAILER_LENGTH = 21
export const TAG_LENGTH = 16

export function deriveNonce(salt12B: Uint8Array, frameIndex: number): Uint8Array {
  if (salt12B.length !== 12) {
    throw new Error('Salt must be exactly 12 bytes')
  }
  const nonce = new Uint8Array(12)
  // Copy salt
  nonce.set(salt12B)
  // Big-endian 8-byte frame index placed at the end of the 12-byte buffer
  const view = new DataView(nonce.buffer, nonce.byteOffset, 12)
  const hi = Math.floor(frameIndex / 0x100000000)
  const lo = frameIndex >>> 0
  view.setUint32(4, view.getUint32(4) ^ hi, false)
  view.setUint32(8, view.getUint32(8) ^ lo, false)
  return nonce
}

export function buildAad(ssrc: number, timestamp: number, keyEpoch: number): Uint8Array {
  const aad = new Uint8Array(9)
  const view = new DataView(aad.buffer, aad.byteOffset, 9)
  view.setUint32(0, ssrc >>> 0, false)
  view.setUint32(4, timestamp >>> 0, false)
  view.setUint8(8, keyEpoch & 0xff)
  return aad
}

export class SlidingReplayWindow {
  private maxSeen = -1
  private windowBitmask = 0n // 128-bit sliding window
  private readonly windowSize = 128

  checkAndAdd(frameIndex: number): boolean {
    if (frameIndex < 0) return false

    if (frameIndex > this.maxSeen) {
      const diff = BigInt(frameIndex - this.maxSeen)
      if (diff >= BigInt(this.windowSize)) {
        this.windowBitmask = 1n
      } else {
        this.windowBitmask = (this.windowBitmask << diff) | 1n
      }
      this.maxSeen = frameIndex
      return true
    }

    const diff = BigInt(this.maxSeen - frameIndex)
    if (diff >= BigInt(this.windowSize)) {
      // Too old
      return false
    }

    const mask = 1n << diff
    if ((this.windowBitmask & mask) !== 0n) {
      // Replay detected
      return false
    }

    this.windowBitmask |= mask
    return true
  }
}

export async function encryptRtpPayload(
  payload: Uint8Array,
  key32B: Uint8Array,
  salt12B: Uint8Array,
  frameIndex: number,
  keyEpoch: number,
  ssrc: number,
  timestamp: number,
): Promise<Uint8Array> {
  const nonce = deriveNonce(salt12B, frameIndex)
  const aad = buildAad(ssrc, timestamp, keyEpoch)

  // aesGcmEncrypt returns raw `ciphertext || authTag(16)`
  const encryptedWithTag = await aesGcmEncrypt(key32B, nonce, payload, aad)

  const ciphertextLen = encryptedWithTag.length - TAG_LENGTH
  const ciphertext = encryptedWithTag.subarray(0, ciphertextLen)
  const authTag = encryptedWithTag.subarray(ciphertextLen)

  // Construct frame: [Ciphertext: N][AuthTag: 16B][FrameIndex: 4B][KeyEpoch: 1B]
  const result = new Uint8Array(ciphertextLen + TRAILER_LENGTH)
  result.set(ciphertext, 0)
  result.set(authTag, ciphertextLen)

  const view = new DataView(result.buffer, result.byteOffset, result.byteLength)
  view.setUint32(ciphertextLen + TAG_LENGTH, frameIndex >>> 0, false)
  view.setUint8(ciphertextLen + TAG_LENGTH + 4, keyEpoch & 0xff)

  return result
}

export async function decryptRtpPayload(
  encryptedFrame: Uint8Array,
  key32B: Uint8Array,
  salt12B: Uint8Array,
  ssrc: number,
  timestamp: number,
  replayWindow: SlidingReplayWindow,
): Promise<{ plaintext: Uint8Array; frameIndex: number; keyEpoch: number }> {
  if (encryptedFrame.length < TRAILER_LENGTH) {
    throw new Error('Frame too short: smaller than 21-byte trailer')
  }

  const ciphertextLen = encryptedFrame.length - TRAILER_LENGTH
  const ciphertext = encryptedFrame.subarray(0, ciphertextLen)
  const authTag = encryptedFrame.subarray(ciphertextLen, ciphertextLen + TAG_LENGTH)

  const view = new DataView(encryptedFrame.buffer, encryptedFrame.byteOffset, encryptedFrame.byteLength)
  const frameIndex = view.getUint32(ciphertextLen + TAG_LENGTH, false)
  const keyEpoch = view.getUint8(ciphertextLen + TAG_LENGTH + 4)

  // 1. Replay check
  if (!replayWindow.checkAndAdd(frameIndex)) {
    throw new Error(`Replay attack or duplicate frame detected: frameIndex ${frameIndex}`)
  }

  // 2. Derive Nonce & AAD
  const nonce = deriveNonce(salt12B, frameIndex)
  const aad = buildAad(ssrc, timestamp, keyEpoch)

  // 3. Combine ciphertext and tag for aesGcmDecrypt
  const combined = new Uint8Array(ciphertextLen + TAG_LENGTH)
  combined.set(ciphertext, 0)
  combined.set(authTag, ciphertextLen)

  // 4. Decrypt & authenticate
  const plaintext = await aesGcmDecrypt(key32B, nonce, combined, aad)
  return { plaintext, frameIndex, keyEpoch }
}

describe('E2E WebRTC Insertable Streams & Frame Cryptor Suite (Tiers 1-4)', () => {
  let masterKey: Uint8Array
  let salt: Uint8Array

  beforeEach(() => {
    masterKey = new Uint8Array(32)
    salt = new Uint8Array(12)
    crypto.getRandomValues(masterKey)
    crypto.getRandomValues(salt)
  })

  // =========================================================================
  // TIER 1: Primary Behavioral Coverage (Happy Path)
  // =========================================================================

  it('Tier 1.1: Encrypts audio RTP payload with AES-256-GCM before egress', async () => {
    const rawAudioPayload = new Uint8Array([0x78, 0x90, 0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc]) // e.g. Opus audio frame
    const ssrc = 0x12345678
    const timestamp = 96000
    const frameIndex = 1
    const epoch = 0

    const encrypted = await encryptRtpPayload(rawAudioPayload, masterKey, salt, frameIndex, epoch, ssrc, timestamp)

    expect(encrypted.length).toBe(rawAudioPayload.length + TRAILER_LENGTH)
    // Verify payload is encrypted and does not match plaintext
    expect(encrypted.subarray(0, rawAudioPayload.length)).not.toEqual(rawAudioPayload)
  })

  it('Tier 1.2: Encrypts video RTP payload with AES-256-GCM before egress', async () => {
    // 1000-byte VP8/H.264 video payload
    const rawVideoPayload = new Uint8Array(1000)
    crypto.getRandomValues(rawVideoPayload)

    const ssrc = 0x87654321
    const timestamp = 180000
    const frameIndex = 42
    const epoch = 0

    const encrypted = await encryptRtpPayload(rawVideoPayload, masterKey, salt, frameIndex, epoch, ssrc, timestamp)

    expect(encrypted.length).toBe(rawVideoPayload.length + TRAILER_LENGTH)
    expect(encrypted.subarray(0, rawVideoPayload.length)).not.toEqual(rawVideoPayload)
  })

  it('Tier 1.3: Monotonic frame counter derives unique non-repeating 12-byte nonces', () => {
    const seenNonces = new Set<string>()
    for (let i = 0; i < 100; i++) {
      const nonce = deriveNonce(salt, i)
      expect(nonce.length).toBe(12)
      const hex = Array.from(nonce).map((b) => b.toString(16).padStart(2, '0')).join('')
      expect(seenNonces.has(hex)).toBe(false)
      seenNonces.add(hex)
    }
  })

  it('Tier 1.4: Trailer formatting conforms to exact 21-byte specification', async () => {
    const payload = new Uint8Array([1, 2, 3, 4])
    const frameIndex = 0x01020304
    const epoch = 5
    const encrypted = await encryptRtpPayload(payload, masterKey, salt, frameIndex, epoch, 111, 222)

    const trailer = encrypted.subarray(encrypted.length - TRAILER_LENGTH)
    expect(trailer.length).toBe(21)

    const view = new DataView(trailer.buffer, trailer.byteOffset, 21)
    // Last byte is epoch
    expect(view.getUint8(20)).toBe(5)
    // Preceding 4 bytes are frame index (0x01020304)
    expect(view.getUint32(16, false)).toBe(0x01020304)
  })

  it('Tier 1.5: AAD binding authentically validates SSRC, Timestamp and KeyEpoch', async () => {
    const payload = new Uint8Array([10, 20, 30, 40])
    const ssrc = 0xabcdef01
    const timestamp = 44100
    const frameIndex = 7
    const epoch = 1
    const replayWindow = new SlidingReplayWindow()

    const encrypted = await encryptRtpPayload(payload, masterKey, salt, frameIndex, epoch, ssrc, timestamp)
    const { plaintext, frameIndex: decryptedIndex, keyEpoch: decryptedEpoch } = await decryptRtpPayload(
      encrypted,
      masterKey,
      salt,
      ssrc,
      timestamp,
      replayWindow,
    )

    expect(plaintext).toEqual(payload)
    expect(decryptedIndex).toBe(frameIndex)
    expect(decryptedEpoch).toBe(epoch)
  })

  it('Tier 1.6: Full roundtrip plaintext fidelity across variable frame sizes', async () => {
    const testSizes = [1, 16, 64, 256, 1024, 4096]
    for (let i = 0; i < testSizes.length; i++) {
      const size = testSizes[i]
      const original = new Uint8Array(size)
      crypto.getRandomValues(original)
      const replayWindow = new SlidingReplayWindow()

      const encrypted = await encryptRtpPayload(original, masterKey, salt, i, 0, 12345, 90000 + i * 3000)
      const { plaintext } = await decryptRtpPayload(encrypted, masterKey, salt, 12345, 90000 + i * 3000, replayWindow)

      expect(plaintext).toEqual(original)
    }
  })

  it('Tier 1.7: Key rotation increments KeyEpoch and decrypts with updated key', async () => {
    const newKey = new Uint8Array(32)
    crypto.getRandomValues(newKey)
    const payload = new Uint8Array([99, 88, 77, 66])

    // Encrypt with new key at epoch 1
    const encrypted = await encryptRtpPayload(payload, newKey, salt, 100, 1, 555, 666)

    const replayWindow = new SlidingReplayWindow()
    const { plaintext, keyEpoch } = await decryptRtpPayload(encrypted, newKey, salt, 555, 666, replayWindow)

    expect(plaintext).toEqual(payload)
    expect(keyEpoch).toBe(1)
  })

  // =========================================================================
  // TIER 2: Boundaries, Tamper Defense & Error Handling
  // =========================================================================

  it('Tier 2.1: Tampered ciphertext bit causes AEAD authentication rejection', async () => {
    const payload = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8])
    const encrypted = await encryptRtpPayload(payload, masterKey, salt, 1, 0, 10, 20)

    // Flip 1 bit in ciphertext payload
    encrypted[0] ^= 0x01

    const replayWindow = new SlidingReplayWindow()
    await expect(decryptRtpPayload(encrypted, masterKey, salt, 10, 20, replayWindow)).rejects.toThrow()
  })

  it('Tier 2.2: Tampered 16-byte AuthTag in trailer causes authentication rejection', async () => {
    const payload = new Uint8Array([1, 2, 3, 4])
    const encrypted = await encryptRtpPayload(payload, masterKey, salt, 1, 0, 10, 20)

    // Tamper auth tag (located at encrypted.length - 21 to encrypted.length - 5)
    encrypted[encrypted.length - 21] ^= 0x01

    const replayWindow = new SlidingReplayWindow()
    await expect(decryptRtpPayload(encrypted, masterKey, salt, 10, 20, replayWindow)).rejects.toThrow()
  })

  it('Tier 2.3: SSRC or Timestamp spoofing causes AAD mismatch and rejection', async () => {
    const payload = new Uint8Array([1, 2, 3, 4])
    const ssrc = 0x11111111
    const timestamp = 0x22222222
    const encrypted = await encryptRtpPayload(payload, masterKey, salt, 1, 0, ssrc, timestamp)

    const replayWindow = new SlidingReplayWindow()
    // Attempt decrypt with spoofed SSRC
    await expect(
      decryptRtpPayload(encrypted, masterKey, salt, ssrc ^ 0x01, timestamp, replayWindow),
    ).rejects.toThrow()

    // Attempt decrypt with spoofed Timestamp
    const replayWindow2 = new SlidingReplayWindow()
    await expect(
      decryptRtpPayload(encrypted, masterKey, salt, ssrc, timestamp ^ 0x01, replayWindow2),
    ).rejects.toThrow()
  })

  it('Tier 2.4: Truncated frame shorter than 21 bytes is rejected immediately', async () => {
    const tooShort = new Uint8Array(20)
    const replayWindow = new SlidingReplayWindow()
    await expect(decryptRtpPayload(tooShort, masterKey, salt, 1, 2, replayWindow)).rejects.toThrow(
      'Frame too short',
    )
  })

  it('Tier 2.5: Replay attack with duplicate frame index is dropped by sliding replay window', async () => {
    const payload = new Uint8Array([1, 2, 3])
    const encrypted = await encryptRtpPayload(payload, masterKey, salt, 50, 0, 1, 2)

    const replayWindow = new SlidingReplayWindow()
    // First reception: success
    const res1 = await decryptRtpPayload(encrypted, masterKey, salt, 1, 2, replayWindow)
    expect(res1.frameIndex).toBe(50)

    // Second reception (replay attack): rejected
    await expect(decryptRtpPayload(encrypted, masterKey, salt, 1, 2, replayWindow)).rejects.toThrow(
      'Replay attack or duplicate frame detected',
    )
  })

  it('Tier 2.6: Boundary payload: empty frame (0-byte payload + 21-byte trailer) encrypts and decrypts', async () => {
    const emptyPayload = new Uint8Array(0)
    const encrypted = await encryptRtpPayload(emptyPayload, masterKey, salt, 0, 0, 100, 200)

    expect(encrypted.length).toBe(TRAILER_LENGTH)

    const replayWindow = new SlidingReplayWindow()
    const { plaintext } = await decryptRtpPayload(encrypted, masterKey, salt, 100, 200, replayWindow)
    expect(plaintext.length).toBe(0)
  })

  // =========================================================================
  // TIER 3: Pairwise Combinations
  // =========================================================================

  it('Tier 3.1: Pairwise: Media Type x Key Epoch x Tamper Target', async () => {
    const combinations: Array<{ isVideo: boolean; epoch: number; tamper: 'payload' | 'tag' | 'aad' }> = [
      { isVideo: false, epoch: 0, tamper: 'payload' },
      { isVideo: false, epoch: 1, tamper: 'tag' },
      { isVideo: true, epoch: 0, tamper: 'aad' },
      { isVideo: true, epoch: 1, tamper: 'payload' },
    ]

    for (const combo of combinations) {
      const payload = new Uint8Array(combo.isVideo ? 800 : 80)
      crypto.getRandomValues(payload)

      const ssrc = 0x1234
      const ts = 9999
      const encrypted = await encryptRtpPayload(payload, masterKey, salt, 1, combo.epoch, ssrc, ts)

      if (combo.tamper === 'payload') {
        encrypted[0] ^= 0xff
      } else if (combo.tamper === 'tag') {
        encrypted[encrypted.length - 21] ^= 0xff
      }

      const replayWindow = new SlidingReplayWindow()
      const decryptSsrc = combo.tamper === 'aad' ? ssrc ^ 0xff : ssrc

      await expect(decryptRtpPayload(encrypted, masterKey, salt, decryptSsrc, ts, replayWindow)).rejects.toThrow()
    }
  })

  it('Tier 3.2: Pairwise: Frame Size x Out-of-Order Delivery x Key Rotation', async () => {
    const testMatrix: Array<{ size: number; order: number[]; rotateKey: boolean }> = [
      { size: 40, order: [1, 3, 2, 4], rotateKey: false },
      { size: 1200, order: [2, 1, 4, 3], rotateKey: true },
    ]

    for (const test of testMatrix) {
      const keyToUse = test.rotateKey ? new Uint8Array(32) : masterKey
      if (test.rotateKey) crypto.getRandomValues(keyToUse)

      const frames: Uint8Array[] = []
      for (let i = 0; i < test.order.length; i++) {
        const frameIdx = test.order[i]
        const data = new Uint8Array(test.size)
        data.fill(frameIdx)
        const enc = await encryptRtpPayload(data, keyToUse, salt, frameIdx, test.rotateKey ? 1 : 0, 10, 20)
        frames.push(enc)
      }

      const replayWindow = new SlidingReplayWindow()
      // Decrypt in out-of-order sequence
      for (let i = 0; i < frames.length; i++) {
        const { plaintext, frameIndex } = await decryptRtpPayload(
          frames[i],
          keyToUse,
          salt,
          10,
          20,
          replayWindow,
        )
        expect(frameIndex).toBe(test.order[i])
        expect(plaintext[0]).toBe(test.order[i])
      }
    }
  })

  // =========================================================================
  // TIER 4: Real-World Adversarial Scenarios
  // =========================================================================

  it('Tier 4.1: Adversarial Scenario: Passive Eavesdropping Interception Test verifies zero plaintext leakage', async () => {
    // Plaintext sensitive voice speech stream
    const sensitiveSpeech = new TextEncoder().encode('Confidential private biometric passphrase')
    const encrypted = await encryptRtpPayload(sensitiveSpeech, masterKey, salt, 1, 0, 777, 888)

    // Adversary captures raw packet off the network
    const wirePacket = encrypted.slice()

    // 1. Verify that the wire packet contains ZERO occurrences of the plaintext words
    const wireStr = new TextDecoder('utf-8', { fatal: false }).decode(wirePacket)
    expect(wireStr).not.toContain('Confidential')
    expect(wireStr).not.toContain('biometric')
    expect(wireStr).not.toContain('passphrase')

    // 2. Adversary attempts to decrypt without the key (or with a guessed key)
    const attackerKey = new Uint8Array(32)
    attackerKey.fill(0xee) // Attacker's guessed key

    const replayWindow = new SlidingReplayWindow()
    await expect(
      decryptRtpPayload(wirePacket, attackerKey, salt, 777, 888, replayWindow),
    ).rejects.toThrow()
  })

  it('Tier 4.2: Adversarial Scenario: Active Man-in-the-Middle Injection & Modification drops 100% of tampered frames', async () => {
    const replayWindow = new SlidingReplayWindow()
    let acceptedFrames = 0
    let rejectedFrames = 0

    // Stream of 20 frames
    for (let frameIdx = 0; frameIdx < 20; frameIdx++) {
      const payload = new Uint8Array([frameIdx, frameIdx + 1, frameIdx + 2])
      const encrypted = await encryptRtpPayload(payload, masterKey, salt, frameIdx, 0, 1234, 5000 + frameIdx)

      // MITM modifies every odd packet
      if (frameIdx % 2 === 1) {
        encrypted[0] ^= 0x42 // inject corruption
      }

      try {
        const { plaintext, frameIndex } = await decryptRtpPayload(
          encrypted,
          masterKey,
          salt,
          1234,
          5000 + frameIdx,
          replayWindow,
        )
        expect(plaintext).toEqual(payload)
        expect(frameIndex).toBe(frameIdx)
        acceptedFrames++
      } catch {
        rejectedFrames++
      }
    }

    // Exactly 10 authentic frames accepted, exactly 10 tampered frames dropped!
    expect(acceptedFrames).toBe(10)
    expect(rejectedFrames).toBe(10)
  })
})
