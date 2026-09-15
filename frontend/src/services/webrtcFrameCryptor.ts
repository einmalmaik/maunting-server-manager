import { aesGcmEncrypt, aesGcmDecrypt } from '@msdis/shield/aead'

export const TRAILER_LENGTH = 21
export const TAG_LENGTH = 16
export const AAD_LENGTH = 9

/**
 * Derives a 12-byte nonce by XORing the frame counter into the salt.
 * Nonce (12B): Salt (12B) XOR FrameCounter (8B big-endian padded to 12B).
 * Bytes 0..3: salt[0..3]
 * Bytes 4..7: salt[4..7] ^ (frameIndex >> 32)
 * Bytes 8..11: salt[8..11] ^ (frameIndex & 0xFFFFFFFF)
 */
export function deriveNonce(salt12B: Uint8Array, frameIndex: number): Uint8Array {
  if (salt12B.length !== 12) {
    throw new Error('Salt must be exactly 12 bytes')
  }
  const nonce = new Uint8Array(12)
  nonce.set(salt12B)
  const view = new DataView(nonce.buffer, nonce.byteOffset, 12)
  const hi = Math.floor(frameIndex / 0x100000000)
  const lo = frameIndex >>> 0
  view.setUint32(4, view.getUint32(4) ^ hi, false)
  view.setUint32(8, view.getUint32(8) ^ lo, false)
  return nonce
}

/**
 * Builds AAD (9B): [SSRC: 4B big-endian][Timestamp: 4B big-endian][KeyEpoch: 1B]
 */
export function buildAad(ssrc: number, timestamp: number, keyEpoch: number): Uint8Array {
  const aad = new Uint8Array(9)
  const view = new DataView(aad.buffer, aad.byteOffset, 9)
  view.setUint32(0, ssrc >>> 0, false)
  view.setUint32(4, timestamp >>> 0, false)
  view.setUint8(8, keyEpoch & 0xff)
  return aad
}

/**
 * 128-bit sliding window for anti-replay defense.
 */
export class SlidingReplayWindow {
  private maxSeen = -1
  private windowBitmask = 0n
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
      return false
    }

    const mask = 1n << diff
    if ((this.windowBitmask & mask) !== 0n) {
      return false
    }

    this.windowBitmask |= mask
    return true
  }

  reset(): void {
    this.maxSeen = -1
    this.windowBitmask = 0n
  }
}

/**
 * Encrypts an RTP frame payload using AES-256-GCM.
 * Output: [Ciphertext: N][AuthTag: 16B][FrameIndex: 4B big-endian][KeyEpoch: 1B]
 */
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

  const result = new Uint8Array(ciphertextLen + TRAILER_LENGTH)
  result.set(ciphertext, 0)
  result.set(authTag, ciphertextLen)

  const view = new DataView(result.buffer, result.byteOffset, result.byteLength)
  view.setUint32(ciphertextLen + TAG_LENGTH, frameIndex >>> 0, false)
  view.setUint8(ciphertextLen + TAG_LENGTH + 4, keyEpoch & 0xff)

  return result
}

/**
 * Decrypts an RTP frame payload and validates anti-replay and authenticity.
 * Returns plaintext, frameIndex, and keyEpoch.
 */
export async function decryptRtpPayload(
  encryptedFrame: Uint8Array,
  key32B: Uint8Array,
  salt12B: Uint8Array,
  ssrc: number,
  timestamp: number,
  replayWindow?: SlidingReplayWindow,
): Promise<{ plaintext: Uint8Array; frameIndex: number; keyEpoch: number }> {
  if (encryptedFrame.length < TRAILER_LENGTH) {
    throw new Error('Frame too short: smaller than 21-byte trailer')
  }

  const ciphertextLen = encryptedFrame.length - TRAILER_LENGTH
  const ciphertext = encryptedFrame.subarray(0, ciphertextLen)
  const authTag = encryptedFrame.subarray(ciphertextLen, ciphertextLen + TAG_LENGTH)

  const view = new DataView(
    encryptedFrame.buffer,
    encryptedFrame.byteOffset + ciphertextLen + TAG_LENGTH,
    5,
  )
  const frameIndex = view.getUint32(0, false)
  const keyEpoch = view.getUint8(4)

  if (replayWindow && !replayWindow.checkAndAdd(frameIndex)) {
    throw new Error(`Replay attack or duplicate frame detected: frameIndex ${frameIndex}`)
  }

  const nonce = deriveNonce(salt12B, frameIndex)
  const aad = buildAad(ssrc, timestamp, keyEpoch)

  const combined = new Uint8Array(ciphertextLen + TAG_LENGTH)
  combined.set(ciphertext, 0)
  combined.set(authTag, ciphertextLen)

  const plaintext = await aesGcmDecrypt(key32B, nonce, combined, aad)
  return { plaintext, frameIndex, keyEpoch }
}

export interface RTCEncodedFrameLike {
  data: ArrayBuffer
  timestamp: number
  type?: 'key' | 'delta' | 'empty'
  getMetadata?: () => {
    ssrc?: number
    payloadType?: number
    synchronizationSource?: number
    csrcs?: number[]
    contributingSources?: number[]
    mimeType?: string
  }
}

export interface CryptorMetrics {
  framesEncrypted: number
  framesDecrypted: number
  framesDropped: number
  replayDropped: number
  authFailed: number
}

/**
 * SenderFrameCryptor handles outbound WebRTC Insertable Streams encryption.
 */
export class SenderFrameCryptor {
  private key: Uint8Array | null = null
  private salt: Uint8Array | null = null
  private frameIndex = 0
  private currentEpoch = 0
  private metrics: CryptorMetrics = {
    framesEncrypted: 0,
    framesDecrypted: 0,
    framesDropped: 0,
    replayDropped: 0,
    authFailed: 0,
  }

  constructor(options?: { key?: Uint8Array; salt?: Uint8Array; initialEpoch?: number }) {
    if (options?.key) this.key = options.key
    if (options?.salt) this.salt = options.salt
    if (typeof options?.initialEpoch === 'number') this.currentEpoch = options.initialEpoch
  }

  setKey(key: Uint8Array, epoch?: number): void {
    this.key = key
    if (typeof epoch === 'number') {
      this.currentEpoch = epoch
    }
  }

  setSalt(salt: Uint8Array): void {
    if (salt.length !== 12) {
      throw new Error('Salt must be exactly 12 bytes')
    }
    this.salt = salt
  }

  rotateKey(newKey: Uint8Array, newSalt?: Uint8Array): number {
    this.key = newKey
    if (newSalt) {
      this.setSalt(newSalt)
    }
    this.currentEpoch = (this.currentEpoch + 1) & 0xff
    return this.currentEpoch
  }

  getFrameIndex(): number {
    return this.frameIndex
  }

  getCurrentEpoch(): number {
    return this.currentEpoch
  }

  getMetrics(): CryptorMetrics {
    return { ...this.metrics }
  }

  async encryptFrame(frame: RTCEncodedFrameLike): Promise<RTCEncodedFrameLike> {
    if (!this.key || !this.salt) {
      throw new Error('SenderFrameCryptor key and salt must be configured before encrypting')
    }

    const payload = new Uint8Array(frame.data)
    const metadata = frame.getMetadata ? frame.getMetadata() : undefined
    const ssrc = metadata?.ssrc ?? metadata?.synchronizationSource ?? 0
    const timestamp = frame.timestamp ?? 0

    const encrypted = await encryptRtpPayload(
      payload,
      this.key,
      this.salt,
      this.frameIndex++,
      this.currentEpoch,
      ssrc,
      timestamp,
    )

    frame.data = encrypted.buffer.slice(
      encrypted.byteOffset,
      encrypted.byteOffset + encrypted.byteLength,
    ) as ArrayBuffer
    this.metrics.framesEncrypted++
    return frame
  }

  createTransformStream(): TransformStream<RTCEncodedFrameLike, RTCEncodedFrameLike> {
    return new TransformStream<RTCEncodedFrameLike, RTCEncodedFrameLike>({
      transform: async (frame, controller) => {
        try {
          const encrypted = await this.encryptFrame(frame)
          controller.enqueue(encrypted)
        } catch {
          this.metrics.framesDropped++
        }
      },
    })
  }

  attach(sender: {
    createEncodedStreams: () => {
      readable: ReadableStream<RTCEncodedFrameLike>
      writable: WritableStream<RTCEncodedFrameLike>
    }
  }): void {
    const { readable, writable } = sender.createEncodedStreams()
    readable.pipeThrough(this.createTransformStream()).pipeTo(writable)
  }
}

/**
 * ReceiverFrameCryptor handles inbound WebRTC Insertable Streams decryption and tamper filtering.
 */
export class ReceiverFrameCryptor {
  private keysByEpoch = new Map<number, { key: Uint8Array; salt: Uint8Array }>()
  private currentEpoch = 0
  private defaultSalt: Uint8Array | null = null
  private replayWindow = new SlidingReplayWindow()
  private metrics: CryptorMetrics = {
    framesEncrypted: 0,
    framesDecrypted: 0,
    framesDropped: 0,
    replayDropped: 0,
    authFailed: 0,
  }

  constructor(options?: { key?: Uint8Array; salt?: Uint8Array; initialEpoch?: number }) {
    if (options?.salt) {
      this.setSalt(options.salt)
    }
    if (options?.key) {
      this.setKey(options.key, options.initialEpoch ?? 0, options.salt)
    }
  }

  setKey(key: Uint8Array, epoch: number = 0, salt?: Uint8Array): void {
    const effectiveSalt = salt || this.defaultSalt
    if (!effectiveSalt) {
      throw new Error('ReceiverFrameCryptor requires salt when setting key')
    }
    this.keysByEpoch.set(epoch & 0xff, { key, salt: effectiveSalt })
    this.currentEpoch = epoch & 0xff
  }

  setSalt(salt: Uint8Array): void {
    if (salt.length !== 12) {
      throw new Error('Salt must be exactly 12 bytes')
    }
    this.defaultSalt = salt
  }

  getCurrentEpoch(): number {
    return this.currentEpoch
  }

  getReplayWindow(): SlidingReplayWindow {
    return this.replayWindow
  }

  getMetrics(): CryptorMetrics {
    return { ...this.metrics }
  }

  async decryptFrame(frame: RTCEncodedFrameLike): Promise<RTCEncodedFrameLike | null> {
    const encryptedBytes = new Uint8Array(frame.data)
    if (encryptedBytes.length < TRAILER_LENGTH) {
      this.metrics.framesDropped++
      return null
    }

    const trailerOffset = encryptedBytes.length - TRAILER_LENGTH
    const view = new DataView(
      encryptedBytes.buffer,
      encryptedBytes.byteOffset + trailerOffset + TAG_LENGTH,
      5,
    )
    const frameIndex = view.getUint32(0, false)
    const keyEpoch = view.getUint8(4)

    // Replay check: drop immediately if duplicate or too old
    if (!this.replayWindow.checkAndAdd(frameIndex)) {
      this.metrics.replayDropped++
      this.metrics.framesDropped++
      return null
    }

    const keyMaterial = this.keysByEpoch.get(keyEpoch) || this.keysByEpoch.get(this.currentEpoch)
    if (!keyMaterial) {
      this.metrics.framesDropped++
      return null
    }

    const metadata = frame.getMetadata ? frame.getMetadata() : undefined
    const ssrc = metadata?.ssrc ?? metadata?.synchronizationSource ?? 0
    const timestamp = frame.timestamp ?? 0

    try {
      const nonce = deriveNonce(keyMaterial.salt, frameIndex)
      const aad = buildAad(ssrc, timestamp, keyEpoch)

      const ciphertextLen = encryptedBytes.length - TRAILER_LENGTH
      const ciphertext = encryptedBytes.subarray(0, ciphertextLen)
      const authTag = encryptedBytes.subarray(ciphertextLen, ciphertextLen + TAG_LENGTH)

      const combined = new Uint8Array(ciphertextLen + TAG_LENGTH)
      combined.set(ciphertext, 0)
      combined.set(authTag, ciphertextLen)

      const plaintext = await aesGcmDecrypt(keyMaterial.key, nonce, combined, aad)
      frame.data = plaintext.buffer.slice(
        plaintext.byteOffset,
        plaintext.byteOffset + plaintext.byteLength,
      ) as ArrayBuffer
      this.metrics.framesDecrypted++
      return frame
    } catch {
      this.metrics.authFailed++
      this.metrics.framesDropped++
      return null
    }
  }

  createTransformStream(): TransformStream<RTCEncodedFrameLike, RTCEncodedFrameLike> {
    return new TransformStream<RTCEncodedFrameLike, RTCEncodedFrameLike>({
      transform: async (frame, controller) => {
        const decrypted = await this.decryptFrame(frame)
        if (decrypted) {
          controller.enqueue(decrypted)
        }
      },
    })
  }

  attach(receiver: {
    createEncodedStreams: () => {
      readable: ReadableStream<RTCEncodedFrameLike>
      writable: WritableStream<RTCEncodedFrameLike>
    }
  }): void {
    const { readable, writable } = receiver.createEncodedStreams()
    readable.pipeThrough(this.createTransformStream()).pipeTo(writable)
  }
}

/**
 * WebRtcFrameCryptor coordinates sender and receiver frame cryptors for a peer connection.
 */
export class WebRtcFrameCryptor {
  private senderCryptors: SenderFrameCryptor[] = []
  private receiverCryptors: ReceiverFrameCryptor[] = []
  private sharedKey: Uint8Array | null = null
  private sharedSalt: Uint8Array | null = null
  private currentEpoch = 0

  constructor(sharedKey?: Uint8Array, sharedSalt?: Uint8Array) {
    if (sharedKey && sharedSalt) {
      this.setKeyAndSalt(sharedKey, sharedSalt)
    }
  }

  setKeyAndSalt(key: Uint8Array, salt: Uint8Array, epoch: number = 0): void {
    this.sharedKey = key
    this.sharedSalt = salt
    this.currentEpoch = epoch & 0xff

    for (const sender of this.senderCryptors) {
      sender.setSalt(salt)
      sender.setKey(key, this.currentEpoch)
    }
    for (const receiver of this.receiverCryptors) {
      receiver.setSalt(salt)
      receiver.setKey(key, this.currentEpoch, salt)
    }
  }

  rotateKey(newKey: Uint8Array, newSalt?: Uint8Array): number {
    this.sharedKey = newKey
    if (newSalt) this.sharedSalt = newSalt
    this.currentEpoch = (this.currentEpoch + 1) & 0xff

    for (const sender of this.senderCryptors) {
      sender.rotateKey(newKey, newSalt)
    }
    for (const receiver of this.receiverCryptors) {
      receiver.setKey(newKey, this.currentEpoch, this.sharedSalt || undefined)
    }
    return this.currentEpoch
  }

  setupSender(sender: {
    createEncodedStreams: () => {
      readable: ReadableStream<RTCEncodedFrameLike>
      writable: WritableStream<RTCEncodedFrameLike>
    }
  }): SenderFrameCryptor {
    const cryptor = new SenderFrameCryptor({
      key: this.sharedKey || undefined,
      salt: this.sharedSalt || undefined,
      initialEpoch: this.currentEpoch,
    })
    cryptor.attach(sender)
    this.senderCryptors.push(cryptor)
    return cryptor
  }

  setupReceiver(receiver: {
    createEncodedStreams: () => {
      readable: ReadableStream<RTCEncodedFrameLike>
      writable: WritableStream<RTCEncodedFrameLike>
    }
  }): ReceiverFrameCryptor {
    const cryptor = new ReceiverFrameCryptor({
      key: this.sharedKey || undefined,
      salt: this.sharedSalt || undefined,
      initialEpoch: this.currentEpoch,
    })
    cryptor.attach(receiver)
    this.receiverCryptors.push(cryptor)
    return cryptor
  }

  getSenderCryptors(): SenderFrameCryptor[] {
    return [...this.senderCryptors]
  }

  getReceiverCryptors(): ReceiverFrameCryptor[] {
    return [...this.receiverCryptors]
  }
}

/**
 * Derives a 32-byte frame key and 12-byte salt from a shared master secret using HKDF-SHA256.
 */
export async function deriveFrameCryptoMaterial(
  masterSecret: Uint8Array,
  saltInput?: Uint8Array,
): Promise<{ key: Uint8Array; salt: Uint8Array }> {
  const baseKey = await crypto.subtle.importKey(
    'raw',
    masterSecret as unknown as BufferSource,
    'HKDF',
    false,
    ['deriveBits'],
  )
  const salt = saltInput && saltInput.length > 0 ? saltInput : new Uint8Array(16)
  const encoder = new TextEncoder()

  const keyBits = await crypto.subtle.deriveBits(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: salt as unknown as BufferSource,
      info: encoder.encode('webrtc-frame-key'),
    },
    baseKey,
    256,
  )

  const saltBits = await crypto.subtle.deriveBits(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: salt as unknown as BufferSource,
      info: encoder.encode('webrtc-frame-salt'),
    },
    baseKey,
    96,
  )

  return {
    key: new Uint8Array(keyBits),
    salt: new Uint8Array(saltBits),
  }
}
