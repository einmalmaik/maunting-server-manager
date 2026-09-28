/**
 * Die Bildseite dessen, was `audioSettings.ts` für den Ton ist.
 *
 * Beide Dateien sind absichtlich gleich gebaut: ein Zustand im localStorage,
 * ein Leser, ein Schreiber und eine Funktion, die daraus fertige
 * `MediaTrackConstraints` macht. Wer aufnimmt, nimmt diese Funktion und erfindet
 * keine eigenen Werte — sonst filmt die Videonotiz anders als der Anruf, und
 * eine Einstellung im Profil bewirkt je nach Fenster etwas anderes.
 *
 * **Warum es kein „Auto" gibt.** Die Vorgabe ist bereits die höchste Stufe, und
 * LiveKit regelt bei schwachem Netz von selbst herunter (`adaptiveStream`,
 * Simulcast). Ein dritter Eintrag, der dasselbe täte wie 1080p, wäre eine
 * Auswahl ohne Unterschied.
 */

/** Die Kantenlänge einer Videonotiz. Rund angezeigt, quadratisch aufgenommen. */
export const VIDEONOTIZ_KANTE = 720

export type VideoAufloesung = '720p' | '1080p' | '1440p' | '2160p'
export type VideoBildrate = 30 | 60

export interface VideoSettings {
  /** Gilt für die Kamera im Anruf. Die Videonotiz ist immer quadratisch. */
  aufloesung: VideoAufloesung
  /** Gilt für beides. */
  bildrate: VideoBildrate
}

const STORAGE_KEYS = {
  aufloesung: 'msm_video_resolution',
  bildrate: 'msm_video_framerate',
} as const

const VORGABE: VideoSettings = {
  aufloesung: '1080p',
  bildrate: 60,
}

export const AUFLOESUNGEN: readonly VideoAufloesung[] = ['720p', '1080p', '1440p', '2160p']

export const MASSE_JE_AUFLOESUNG: Record<VideoAufloesung, { width: number; height: number }> = {
  '720p': { width: 1280, height: 720 },
  '1080p': { width: 1920, height: 1080 },
  '1440p': { width: 2560, height: 1440 },
  '2160p': { width: 3840, height: 2160 },
}

/**
 * Zielbitraten der Kamera im Anruf, bei 60 Bildern je Sekunde.
 *
 * Deutlich über den LiveKit-Vorgaben — dort steht 720p bei 1,7 und 2160p bei
 * 8 Mbit/s, und das ist für echtes 4K zu wenig: bei 8 Mbit/s wird ein bewegtes
 * Bild in 3840×2160 matschig, und dann ist die Auflösung nur eine Zahl. Bei
 * 30 FPS wird halbiert.
 *
 * Das ist eine Obergrenze, keine Zusage. Trägt die Leitung sie nicht, nimmt
 * LiveKit über Simulcast und `adaptiveStream` von selbst zurück.
 */
const BITRATE_JE_AUFLOESUNG: Record<VideoAufloesung, number> = {
  '720p': 3_000_000,
  '1080p': 6_000_000,
  '1440p': 12_000_000,
  '2160p': 25_000_000,
}

export function getVideoSettings(): VideoSettings {
  try {
    const aufloesung = localStorage.getItem(STORAGE_KEYS.aufloesung) as VideoAufloesung | null
    const bildrate = Number(localStorage.getItem(STORAGE_KEYS.bildrate))
    return {
      aufloesung:
        aufloesung && AUFLOESUNGEN.includes(aufloesung) ? aufloesung : VORGABE.aufloesung,
      bildrate: bildrate === 30 || bildrate === 60 ? bildrate : VORGABE.bildrate,
    }
  } catch {
    return { ...VORGABE }
  }
}

export function saveVideoSettings(settings: Partial<VideoSettings>): void {
  try {
    if (settings.aufloesung !== undefined) {
      localStorage.setItem(STORAGE_KEYS.aufloesung, settings.aufloesung)
    }
    if (settings.bildrate !== undefined) {
      localStorage.setItem(STORAGE_KEYS.bildrate, String(settings.bildrate))
    }
  } catch {
    // Ein Browser ohne Speicher bekommt die Vorgabe, nicht einen Absturz.
  }
}

/** Auflösung und Bildrate der Kamera im Anruf. */
export function getVideoCaptureAufloesung(): { width: number; height: number; frameRate: number } {
  const settings = getVideoSettings()
  return { ...MASSE_JE_AUFLOESUNG[settings.aufloesung], frameRate: settings.bildrate }
}

/** Obergrenzen fürs Senden im Anruf. */
export function getVideoSendeGrenzen(): { maxBitrate: number; maxFramerate: number } {
  const settings = getVideoSettings()
  return {
    maxBitrate: BITRATE_JE_AUFLOESUNG[settings.aufloesung] * (settings.bildrate >= 60 ? 1 : 0.5),
    maxFramerate: settings.bildrate,
  }
}

/**
 * Die Kamera für eine Videonotiz: quadratisch, nach vorn, Bildrate wie gewählt.
 *
 * `ideal` statt `exact`, damit eine Kamera ohne quadratischen Modus das
 * nächstbeste Bild gibt statt mit `OverconstrainedError` abzubrechen —
 * angezeigt wird ohnehin ein Kreis mit `object-cover`.
 *
 * **Das `max` ist der Grund, warum hier eine Zahl doppelt steht.** Auf einer
 * Webcam mit 2560×1440 lieferte `ideal: 720` allein ein Bild in 1440×1440: der
 * Wunsch ist kein Deckel, und die Kamera nahm ihren besten Modus. Das sind
 * viermal so viele Bildpunkte, die bei gleicher Bitrate kodiert werden müssen —
 * teurer beim Aufnehmen und weicher im Ergebnis, für einen Kreis von wenigen
 * Zentimetern. Die Bildrate bleibt ohne Obergrenze: mehr Bilder sind hier
 * tatsächlich besser.
 */
export function getVideoNoteConstraints(): MediaTrackConstraints {
  return {
    facingMode: 'user',
    width: { ideal: VIDEONOTIZ_KANTE, max: VIDEONOTIZ_KANTE },
    height: { ideal: VIDEONOTIZ_KANTE, max: VIDEONOTIZ_KANTE },
    frameRate: { ideal: getVideoSettings().bildrate },
  }
}
