/**
 * Die Rechnerei hinter der runden Videonotiz: Fortschrittsring und Datenbudget.
 *
 * Die Datei hieß bis 09/2026 `videoNotizGesten.ts` und trug eine
 * Wischauswertung mit. Die ist weg: geöffnet wird der Rekorder per Klick, nicht
 * per Halten, und die Gesten am Vollbildrahmen fingen die Berührung des
 * Sendeknopfs ab. Geblieben ist, was wirklich gerechnet werden muss.
 */

import { maxAnhangBytes } from '@/services/medienKrypto'

export const MAX_VIDEO_NOTE_DURATION_SEC = 60

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

/** Ton der Videonotiz. Opus in Stereo braucht nicht mehr, und es reist mit. */
const AUDIO_BITRATE = 128_000

/**
 * Die Anteile aus dem Budget zurückgerechnet, statt eine Zahl zu erfinden.
 *
 * Eine volle Minute muss durch den Anhangdeckel passen. Ohne Vorgabe wählt der
 * Browser die Bitrate selbst, und bei einer langen Notiz lag sie über dem, was
 * der Upload annimmt — die Aufnahme lief dann durch und verschwand am Server.
 *
 * Die 85 Prozent sind Luft für Spitzen: die Bitrate ist ein Mittelwert, und ein
 * bewegtes Bild zieht kurzzeitig darüber. Reißt es trotzdem, greift der
 * Größenwächter im Rekorder.
 */
export function videoNotizBitraten(): { video: number; audio: number } {
  const budgetBits = videoNotizBudgetBytes() * 8
  const gesamt = budgetBits / MAX_VIDEO_NOTE_DURATION_SEC
  return {
    video: Math.max(500_000, Math.floor(gesamt - AUDIO_BITRATE)),
    audio: AUDIO_BITRATE,
  }
}

/**
 * Wann der Rekorder von selbst abbricht.
 *
 * **Bewusst unter `maxAnhangBytes()`.** Der Wächter schlägt erst an, *nachdem*
 * ein Stück das Budget erreicht hat; der fertige Blob liegt also immer etwas
 * darüber. Stünden beide Zahlen gleich, wiese die Prüfung im Sendepfad genau
 * die Aufnahmen ab, die der Wächter gerade gerettet hat.
 *
 * Die zehn Prozent decken das letzte Stück und den Abschluss des Muxers. Was
 * hier durchgeht, passt im Sendepfad sicher durch.
 */
export function videoNotizBudgetBytes(): number {
  return Math.floor(maxAnhangBytes() * 0.9)
}
