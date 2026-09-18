/**
 * Die Gestensteuerung der Videonotiz: wischen, sperren, abbrechen.
 *
 * Reine Rechnerei ohne Zustand. Sie stand bis 09/2026 in
 * `services/videoNoteCrypto.ts`, zusammen mit einem handgeschriebenen AES-GCM
 * für Videonotizen. Die Krypto ist weg — Anhänge laufen alle über
 * `services/medienKrypto.ts` —, die Gesten sind geblieben und gehören hierher.
 */

export const GESTURE_LOCK_THRESHOLD_PX = 50
export const GESTURE_CANCEL_THRESHOLD_PX = -50
export const MAX_VIDEO_NOTE_DURATION_SEC = 60

/** Entscheidet aus der Wischbewegung, ob die Aufnahme einrastet oder abbricht. */
export function evaluateSwipeGesture(
  deltaX: number,
  deltaY: number
): 'lock_video' | 'cancel' | 'recording_audio' {
  if (deltaX < GESTURE_CANCEL_THRESHOLD_PX) {
    return 'cancel'
  }
  if (deltaY < -GESTURE_LOCK_THRESHOLD_PX) {
    return 'lock_video'
  }
  return 'recording_audio'
}

/** Rechnet die verstrichene Zeit in den Versatz des SVG-Fortschrittsrings um. */
export function calculateProgressRingOffset(
  elapsedSeconds: number,
  maxSeconds: number = MAX_VIDEO_NOTE_DURATION_SEC,
  radius: number = 40
): { circumference: number; strokeDashoffset: number; progressPercent: number } {
  const circumference = 2 * Math.PI * radius
  const clampedElapsed = Math.min(Math.max(elapsedSeconds, 0), maxSeconds)
  const progressPercent = (clampedElapsed / maxSeconds) * 100
  const strokeDashoffset = circumference - (clampedElapsed / maxSeconds) * circumference
  return { circumference, strokeDashoffset, progressPercent }
}
