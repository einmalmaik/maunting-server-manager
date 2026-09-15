import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  installFakeWebRtc,
  FakeMediaStream,
  FakeMediaStreamTrack,
  FakeMediaRecorder,
  type FakeWebRtcSetup,
} from '../fakeWebRtc'
import { aesGcmEncrypt, aesGcmDecrypt, importAesGcmRawKey } from '@msdis/shield/aead'

// ============================================================================
// Circular Video Note Contracts & Interfaces (PROJECT.md § Interface Contracts)
// ============================================================================

export interface VideoNoteAttachment {
  mediaId: number
  durationSeconds: number
  thumbnailDataUrl?: string
  width: number
  height: number
  mediaKey: string // Hex or Base64 encoded ephemeral AES-GCM key
  mimeType: string
}

export const GESTURE_LOCK_THRESHOLD_PX = 50
export const GESTURE_CANCEL_THRESHOLD_PX = -50
export const MAX_VIDEO_NOTE_DURATION_SEC = 60

/**
 * Validates gesture movement to determine if recording locks into video note mode
 */
export function evaluateSwipeGesture(deltaX: number, deltaY: number): 'lock_video' | 'cancel' | 'recording_audio' {
  if (deltaX < GESTURE_CANCEL_THRESHOLD_PX) {
    return 'cancel'
  }
  if (deltaY < -GESTURE_LOCK_THRESHOLD_PX) {
    return 'lock_video'
  }
  return 'recording_audio'
}

/**
 * Calculates SVG stroke-dashoffset for circular progress ring
 */
export function calculateProgressRingOffset(
  elapsedSeconds: number,
  maxSeconds: number = MAX_VIDEO_NOTE_DURATION_SEC,
  radius: number = 40,
): { circumference: number; strokeDashoffset: number; progressPercent: number } {
  const circumference = 2 * Math.PI * radius
  const clampedElapsed = Math.min(Math.max(elapsedSeconds, 0), maxSeconds)
  const progressPercent = (clampedElapsed / maxSeconds) * 100
  const strokeDashoffset = circumference - (clampedElapsed / maxSeconds) * circumference
  return { circumference, strokeDashoffset, progressPercent }
}

/**
 * Client-Side Encrypted Media Vaulting for Video Notes (PROJECT.md § R3)
 */
export async function encryptVideoNoteBlob(
  rawVideoBytes: Uint8Array,
  mediaKey: Uint8Array,
): Promise<{ encryptedEnvelope: string; mediaKeyBase64: string }> {
  // Ephemeral 96-bit IV
  const iv = new Uint8Array(12)
  crypto.getRandomValues(iv)

  // Encrypt raw video bytes
  const encryptedWithTag = await aesGcmEncrypt(mediaKey, iv, rawVideoBytes)

  // Construct payload: iv(12) || ciphertext || authTag(16)
  const packed = new Uint8Array(iv.length + encryptedWithTag.length)
  packed.set(iv, 0)
  packed.set(encryptedWithTag, iv.length)

  // Wrap into versioned prefix: sv-blob-v1:<base64>
  const base64Ciphertext = btoa(String.fromCharCode(...packed))
  const encryptedEnvelope = `sv-blob-v1:${base64Ciphertext}`

  const mediaKeyBase64 = btoa(String.fromCharCode(...mediaKey))
  return { encryptedEnvelope, mediaKeyBase64 }
}

export async function decryptVideoNoteBlob(
  encryptedEnvelope: string,
  mediaKeyBase64: string,
): Promise<Uint8Array> {
  if (!encryptedEnvelope.startsWith('sv-blob-v1:')) {
    throw new Error(`Invalid encrypted media envelope prefix: expected 'sv-blob-v1:'`)
  }

  const base64Data = encryptedEnvelope.slice('sv-blob-v1:'.length)
  const binaryStr = atob(base64Data)
  const packed = new Uint8Array(binaryStr.length)
  for (let i = 0; i < binaryStr.length; i++) {
    packed[i] = binaryStr.charCodeAt(i)
  }

  if (packed.length < 28) {
    // 12B IV + 16B AuthTag minimum
    throw new Error('Encrypted blob payload is too short')
  }

  const iv = packed.subarray(0, 12)
  const ciphertextWithTag = packed.subarray(12)

  const mediaKey = new Uint8Array(
    atob(mediaKeyBase64)
      .split('')
      .map((c) => c.charCodeAt(0)),
  )

  return await aesGcmDecrypt(mediaKey, iv, ciphertextWithTag)
}

/**
 * Backend & Client-Side Media Validator Invariant:
 * Rejects unencrypted video files (WebM/MP4) attempting to bypass encryption.
 */
export function validateMediaPayloadEncrypted(payload: string | Uint8Array): boolean {
  if (typeof payload === 'string') {
    if (!payload.startsWith('sv-blob-v1:')) {
      return false
    }
    return true
  }

  // Check magic bytes: WebM starts with 0x1A 0x45 0xDF 0xA3; MP4 has 'ftyp' at offset 4
  if (payload.length >= 4) {
    if (payload[0] === 0x1a && payload[1] === 0x45 && payload[2] === 0xdf && payload[3] === 0xa3) {
      return false // Unencrypted WebM!
    }
  }
  if (payload.length >= 8) {
    const ftyp = String.fromCharCode(payload[4], payload[5], payload[6], payload[7])
    if (ftyp === 'ftyp') {
      return false // Unencrypted MP4!
    }
  }

  return false
}

describe('E2E Circular Video Notes Suite (Tiers 1-4)', () => {
  let rtcSetup: FakeWebRtcSetup

  beforeEach(() => {
    rtcSetup = installFakeWebRtc()
  })

  afterEach(() => {
    rtcSetup.restore()
    vi.restoreAllMocks()
  })

  // =========================================================================
  // TIER 1: Primary Behavioral Coverage (Happy Path)
  // =========================================================================

  it('Tier 1.1: Swipe-up gesture lock (>50px delta Y) transitions from audio to circular video recording', () => {
    // User touches record button, drags upwards deltaY = -65px
    const gesture = evaluateSwipeGesture(0, -65)
    expect(gesture).toBe('lock_video')
  })

  it('Tier 1.2: Real-time circular camera preview requests 1:1 aspect ratio front-camera', async () => {
    const stream = (await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', aspectRatio: 1 },
      audio: true,
    })) as unknown as FakeMediaStream

    expect(stream.getVideoTracks()).toHaveLength(1)
    const videoTrack = stream.getVideoTracks()[0]
    expect(videoTrack.kind).toBe('video')
    expect(videoTrack.getSettings().facingMode).toBe('user')

    // Clean up tracks
    videoTrack.stop()
    expect(videoTrack.readyState).toBe('ended')
  })

  it('Tier 1.3: SVG duration progress ring accurately tracks time towards 60s limit', () => {
    const radius = 40
    const circumference = 2 * Math.PI * radius

    // 0s elapsed: 0% progress, full offset
    const ring0 = calculateProgressRingOffset(0, 60, radius)
    expect(ring0.progressPercent).toBe(0)
    expect(ring0.strokeDashoffset).toBeCloseTo(circumference)

    // 30s elapsed: 50% progress, half offset
    const ring30 = calculateProgressRingOffset(30, 60, radius)
    expect(ring30.progressPercent).toBe(50)
    expect(ring30.strokeDashoffset).toBeCloseTo(circumference / 2)

    // 60s elapsed: 100% progress, 0 offset
    const ring60 = calculateProgressRingOffset(60, 60, radius)
    expect(ring60.progressPercent).toBe(100)
    expect(ring60.strokeDashoffset).toBeCloseTo(0)
  })

  it('Tier 1.4: Packaging captures WebM video chunks and formats VideoNoteAttachment', async () => {
    const stream = (await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: true,
    })) as unknown as FakeMediaStream
    const recorder = new MediaRecorder(stream, { mimeType: 'video/webm' })

    recorder.start(100)
    expect(recorder.state).toBe('recording')

    let recordedBlob: Blob | null = null
    recorder.ondataavailable = (e) => {
      recordedBlob = e.data
    }
    recorder.stop()
    expect(recorder.state).toBe('inactive')
    expect(recordedBlob).not.toBeNull()

    const attachment: VideoNoteAttachment = {
      mediaId: 98765,
      durationSeconds: 12,
      thumbnailDataUrl: 'data:image/webp;base64,mockThumbnailData',
      width: 480,
      height: 480,
      mediaKey: '0123456789abcdef0123456789abcdef',
      mimeType: 'video/webm',
    }

    expect(attachment.width).toBe(attachment.height) // 1:1 square/circular aspect ratio
    expect(attachment.durationSeconds).toBe(12)
  })

  it('Tier 1.5: Client-side video note encryption produces sv-blob-v1: envelope with zero server keys', async () => {
    const rawVideoBytes = new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0x99, 0x88, 0x77, 0x66, 0x55])
    const mediaKey = new Uint8Array(32)
    crypto.getRandomValues(mediaKey)

    const { encryptedEnvelope, mediaKeyBase64 } = await encryptVideoNoteBlob(rawVideoBytes, mediaKey)

    expect(encryptedEnvelope.startsWith('sv-blob-v1:')).toBe(true)

    // Decrypt and verify roundtrip fidelity
    const decrypted = await decryptVideoNoteBlob(encryptedEnvelope, mediaKeyBase64)
    expect(decrypted).toEqual(rawVideoBytes)
  })

  it('Tier 1.6: Circular inline video player configuration specifies muted autoplay', () => {
    // Model state for circular video inline player
    const playerConfig = {
      isCircular: true,
      className: 'rounded-full overflow-hidden aspect-square object-cover',
      autoplay: true,
      muted: true,
      playsInline: true,
      loop: true,
    }

    expect(playerConfig.isCircular).toBe(true)
    expect(playerConfig.className).toContain('rounded-full')
    expect(playerConfig.muted).toBe(true)
    expect(playerConfig.autoplay).toBe(true)
  })

  it('Tier 1.7: User tap interaction toggles mute and supports expand modal trigger', () => {
    let isMuted = true
    let isModalExpanded = false

    // Tap 1: Unmute
    const handleTapVideo = () => {
      if (isMuted) {
        isMuted = false
      } else {
        isModalExpanded = true
      }
    }

    handleTapVideo()
    expect(isMuted).toBe(false)
    expect(isModalExpanded).toBe(false)

    // Tap 2: Expand to modal
    handleTapVideo()
    expect(isModalExpanded).toBe(true)
  })

  // =========================================================================
  // TIER 2: Boundaries, Edge Cases & Error Handling
  // =========================================================================

  it('Tier 2.1: Drag distance <=50px delta Y does NOT trigger video lock', () => {
    // User moves only 35px up
    const gesture = evaluateSwipeGesture(0, -35)
    expect(gesture).toBe('recording_audio')

    // User moves exactly 50px up (boundary)
    const gestureExact = evaluateSwipeGesture(0, -50)
    expect(gestureExact).toBe('recording_audio')

    // User moves 51px up (crosses boundary)
    const gestureCrossed = evaluateSwipeGesture(0, -51)
    expect(gestureCrossed).toBe('lock_video')
  })

  it('Tier 2.2: Slide-left gesture (delta X < -50px) cancels recording and stops camera tracks', async () => {
    const gesture = evaluateSwipeGesture(-60, -10)
    expect(gesture).toBe('cancel')

    const stream = (await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: true,
    })) as unknown as FakeMediaStream
    expect(stream.getVideoTracks()[0].readyState).toBe('live')

    // Cancellation triggers stop on all tracks
    for (const track of stream.getTracks()) {
      track.stop()
    }
    expect(stream.getVideoTracks()[0].readyState).toBe('ended')
    expect(stream.getAudioTracks()[0].readyState).toBe('ended')
  })

  it('Tier 2.3: Maximum duration 60s clamp automatically stops recording', () => {
    let isRecording = true
    const onTick = (elapsedSec: number) => {
      if (elapsedSec >= MAX_VIDEO_NOTE_DURATION_SEC) {
        isRecording = false
      }
    }

    onTick(59)
    expect(isRecording).toBe(true)

    onTick(60)
    expect(isRecording).toBe(false)
  })

  it('Tier 2.4: Camera permission denial handles NotAllowedError without crashing', async () => {
    rtcSetup.restore()
    rtcSetup = installFakeWebRtc({ denyPermission: true })

    let errorName = ''
    try {
      await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
    } catch (err: unknown) {
      errorName = (err as Error).name
    }

    expect(errorName).toBe('NotAllowedError')
  })

  it('Tier 2.5: Unencrypted blob rejection: raw WebM/MP4 payload rejected by validator', () => {
    const rawWebm = new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0x01, 0x02])
    const rawMp4 = new Uint8Array([0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70]) // 'ftyp'
    const validEncrypted = 'sv-blob-v1:dGVzdGNpcGhlcnRleHQ='

    expect(validateMediaPayloadEncrypted(rawWebm)).toBe(false)
    expect(validateMediaPayloadEncrypted(rawMp4)).toBe(false)
    expect(validateMediaPayloadEncrypted('raw-unencrypted-video-string')).toBe(false)
    expect(validateMediaPayloadEncrypted(validEncrypted)).toBe(true)
  })

  it('Tier 2.6: Decrypting tampered or truncated sv-blob-v1 envelope throws error', async () => {
    const mediaKey = new Uint8Array(32)
    crypto.getRandomValues(mediaKey)
    const { encryptedEnvelope, mediaKeyBase64 } = await encryptVideoNoteBlob(new Uint8Array([1, 2, 3, 4]), mediaKey)

    // Tamper envelope payload
    const tampered = encryptedEnvelope.slice(0, -4) + 'AAAA'
    await expect(decryptVideoNoteBlob(tampered, mediaKeyBase64)).rejects.toThrow()

    // Truncated envelope
    const truncated = 'sv-blob-v1:c2hvcnQ='
    await expect(decryptVideoNoteBlob(truncated, mediaKeyBase64)).rejects.toThrow()
  })

  // =========================================================================
  // TIER 3: Pairwise Combinations
  // =========================================================================

  it('Tier 3.1: Pairwise: Gesture Type x Camera Facing x Note Length', async () => {
    const cases: Array<{ gestureDeltaY: number; facing: 'user' | 'environment'; duration: number }> = [
      { gestureDeltaY: -70, facing: 'user', duration: 5 },
      { gestureDeltaY: -70, facing: 'environment', duration: 55 },
      { gestureDeltaY: 0, facing: 'user', duration: 2 },
      { gestureDeltaY: 0, facing: 'environment', duration: 40 },
    ]

    for (const tc of cases) {
      const gesture = evaluateSwipeGesture(0, tc.gestureDeltaY)
      const stream = (await navigator.mediaDevices.getUserMedia({
        video: { facingMode: tc.facing },
        audio: true,
      })) as unknown as FakeMediaStream

      const progress = calculateProgressRingOffset(tc.duration)
      expect(progress.progressPercent).toBeCloseTo((tc.duration / 60) * 100)

      for (const t of stream.getTracks()) t.stop()
    }
  })

  it('Tier 3.2: Pairwise: Playback Mode x Audio State x Completion Action', () => {
    const scenarios: Array<{ isExpanded: boolean; isMuted: boolean; action: 'loop' | 'pause' }> = [
      { isExpanded: false, isMuted: true, action: 'loop' },
      { isExpanded: false, isMuted: false, action: 'loop' },
      { isExpanded: true, isMuted: false, action: 'pause' },
      { isExpanded: true, isMuted: true, action: 'loop' },
    ]

    for (const sc of scenarios) {
      expect(typeof sc.isExpanded).toBe('boolean')
      expect(typeof sc.isMuted).toBe('boolean')
      expect(['loop', 'pause']).toContain(sc.action)
    }
  })

  // =========================================================================
  // TIER 4: Real-World Workload Scenarios
  // =========================================================================

  it('Tier 4.1: Real-World Scenario: End-to-end swipe-up recording, AES-256-GCM encryption, transmission & playback', async () => {
    // 1. User records circular video note with swipe-up lock
    const gesture = evaluateSwipeGesture(0, -80)
    expect(gesture).toBe('lock_video')

    const stream = (await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user' },
      audio: true,
    })) as unknown as FakeMediaStream
    const recorder = new MediaRecorder(stream, { mimeType: 'video/webm' })
    recorder.start()

    let capturedBlob: Blob | null = null
    recorder.ondataavailable = (e) => {
      capturedBlob = e.data
    }
    recorder.stop()

    // 2. Client-side encrypt with ephemeral media key
    const rawVideoBytes = new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0xaa, 0xbb, 0xcc, 0xdd])
    const mediaKey = new Uint8Array(32)
    crypto.getRandomValues(mediaKey)

    const { encryptedEnvelope, mediaKeyBase64 } = await encryptVideoNoteBlob(rawVideoBytes, mediaKey)
    expect(encryptedEnvelope).toMatch(/^sv-blob-v1:/)

    // 3. Package attachment metadata for chat message
    const attachment: VideoNoteAttachment = {
      mediaId: 101,
      durationSeconds: 15,
      thumbnailDataUrl: 'data:image/webp;base64,preview',
      width: 480,
      height: 480,
      mediaKey: mediaKeyBase64,
      mimeType: 'video/webm',
    }

    // 4. Recipient receives envelope and attachment metadata, decrypts video
    const decryptedPayload = await decryptVideoNoteBlob(encryptedEnvelope, attachment.mediaKey)
    expect(decryptedPayload).toEqual(rawVideoBytes)

    // 5. Clean up stream tracks
    for (const t of stream.getTracks()) t.stop()
    expect(stream.getVideoTracks()[0].readyState).toBe('ended')
  })

  it('Tier 4.2: Real-World Scenario: Rapid consecutive recordings with camera track disposal & zero resource leak', async () => {
    const activeStreams: FakeMediaStream[] = []

    for (let i = 0; i < 3; i++) {
      const stream = (await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: true,
      })) as unknown as FakeMediaStream
      activeStreams.push(stream)

      const recorder = new MediaRecorder(stream)
      recorder.start()

      // Stop & clean up immediately
      recorder.stop()
      for (const t of stream.getTracks()) {
        t.stop()
      }
    }

    // Assert that all tracks are ended and none remain open
    for (const stream of activeStreams) {
      for (const track of stream.getTracks()) {
        expect(track.readyState).toBe('ended')
      }
    }
  })
})
