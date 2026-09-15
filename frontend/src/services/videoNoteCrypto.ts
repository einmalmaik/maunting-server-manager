import { aesGcmEncrypt, aesGcmDecrypt } from '@msdis/shield/aead'

export interface VideoNoteAttachment {
  mediaId: number
  durationSeconds: number
  thumbnailDataUrl?: string
  width: number
  height: number
  mediaKey: string // Base64 encoded ephemeral AES-GCM key
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
  const iv = new Uint8Array(12)
  crypto.getRandomValues(iv)

  const encryptedWithTag = await aesGcmEncrypt(mediaKey, iv, rawVideoBytes)

  const packed = new Uint8Array(iv.length + encryptedWithTag.length)
  packed.set(iv, 0)
  packed.set(encryptedWithTag, iv.length)

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
    throw new Error(`Encrypted blob payload truncated: minimum length is 28 bytes`)
  }

  const iv = packed.subarray(0, 12)
  const ciphertextWithTag = packed.subarray(12)

  const keyBinaryStr = atob(mediaKeyBase64)
  const mediaKey = new Uint8Array(keyBinaryStr.length)
  for (let i = 0; i < keyBinaryStr.length; i++) {
    mediaKey[i] = keyBinaryStr.charCodeAt(i)
  }

  return await aesGcmDecrypt(mediaKey, iv, ciphertextWithTag)
}
