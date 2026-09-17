/**
 * Zentrales Audio- und Mikrofoneinstellungs-Modul.
 *
 * Einheitliche Konfiguration für WebRTC-Audioverarbeitung:
 * - Rauschunterdrückung (noiseSuppression)
 * - Echounterdrückung (echoCancellation)
 * - Automatische Pegelanpassung (autoGainControl)
 * - Bevorzugtes Eingabegerät (deviceId)
 * - Bevorzugtes Ausgabegerät (sinkId) und Software-Eingangsverstärkung
 *
 * Wird projektweit sowohl vom Wake-Word / Sprachassistenten als auch von der
 * Messenger-Sprachaufnahme, den Anrufen und dem Profil-Mikrofontest genutzt.
 */

/** Grenzen der Software-Verstärkung. Gleich denen in `audioGeraete.ts`. */
export const GAIN_MIN = 0.25
export const GAIN_MAX = 4

export interface AudioSettings {
  noiseSuppression: boolean
  echoCancellation: boolean
  autoGainControl: boolean
  preferredMicId: string | null
  preferredSpeakerId: string | null
  /** Software-Eingangsverstärkung, 1 = neutral. Bereits geklemmt. */
  micGain: number
}

const STORAGE_KEYS = {
  noiseSuppression: 'msm_audio_noise_suppression',
  echoCancellation: 'msm_audio_echo_cancellation',
  autoGainControl: 'msm_audio_auto_gain',
  preferredMicId: 'msm_preferred_mic_id',
  preferredSpeakerId: 'msm_preferred_speaker_id',
  micGain: 'msm_audio_mic_gain',
} as const

const VORGABE: AudioSettings = {
  noiseSuppression: true,
  echoCancellation: true,
  autoGainControl: true,
  preferredMicId: null,
  preferredSpeakerId: null,
  micGain: 1,
}

function klemmeGain(roh: number): number {
  if (!Number.isFinite(roh)) return 1
  return Math.min(GAIN_MAX, Math.max(GAIN_MIN, roh))
}

export function getAudioSettings(): AudioSettings {
  try {
    const micId = localStorage.getItem(STORAGE_KEYS.preferredMicId)
    const speakerId = localStorage.getItem(STORAGE_KEYS.preferredSpeakerId)
    const gainRoh = localStorage.getItem(STORAGE_KEYS.micGain)
    const noiseSuppression = localStorage.getItem(STORAGE_KEYS.noiseSuppression) !== 'false'
    const echoCancellation = localStorage.getItem(STORAGE_KEYS.echoCancellation) !== 'false'
    const autoGainControl = localStorage.getItem(STORAGE_KEYS.autoGainControl) !== 'false'

    return {
      noiseSuppression,
      echoCancellation,
      autoGainControl,
      preferredMicId: micId && micId.trim() ? micId.trim() : null,
      preferredSpeakerId: speakerId && speakerId.trim() ? speakerId.trim() : null,
      micGain: gainRoh === null ? 1 : klemmeGain(Number(gainRoh)),
    }
  } catch {
    return { ...VORGABE }
  }
}

export function saveAudioSettings(settings: Partial<AudioSettings>): void {
  try {
    if (settings.noiseSuppression !== undefined) {
      localStorage.setItem(STORAGE_KEYS.noiseSuppression, String(settings.noiseSuppression))
    }
    if (settings.echoCancellation !== undefined) {
      localStorage.setItem(STORAGE_KEYS.echoCancellation, String(settings.echoCancellation))
    }
    if (settings.autoGainControl !== undefined) {
      localStorage.setItem(STORAGE_KEYS.autoGainControl, String(settings.autoGainControl))
    }
    if (settings.preferredMicId !== undefined) {
      if (settings.preferredMicId) {
        localStorage.setItem(STORAGE_KEYS.preferredMicId, settings.preferredMicId)
      } else {
        localStorage.removeItem(STORAGE_KEYS.preferredMicId)
      }
    }
    if (settings.preferredSpeakerId !== undefined) {
      if (settings.preferredSpeakerId) {
        localStorage.setItem(STORAGE_KEYS.preferredSpeakerId, settings.preferredSpeakerId)
      } else {
        localStorage.removeItem(STORAGE_KEYS.preferredSpeakerId)
      }
    }
    if (settings.micGain !== undefined) {
      localStorage.setItem(STORAGE_KEYS.micGain, String(klemmeGain(settings.micGain)))
    }
  } catch {
    // Non-blocking storage fallback
  }
}

export function getAudioTrackConstraints(): MediaTrackConstraints {
  const settings = getAudioSettings()
  return {
    ...(settings.preferredMicId ? { deviceId: { ideal: settings.preferredMicId } } : {}),
    echoCancellation: settings.echoCancellation,
    noiseSuppression: settings.noiseSuppression,
    autoGainControl: settings.autoGainControl,
    channelCount: 1,
  }
}
