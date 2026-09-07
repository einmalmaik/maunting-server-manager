/**
 * Zentrales Audio- und Mikrofoneinstellungs-Modul.
 *
 * Einheitliche Konfiguration für WebRTC-Audioverarbeitung:
 * - Rauschunterdrückung (noiseSuppression)
 * - Echounterdrückung (echoCancellation)
 * - Automatische Pegelanpassung (autoGainControl)
 * - Bevorzugtes Eingabegerät (deviceId)
 *
 * Wird projektweit sowohl vom Wake-Word / Sprachassistenten als auch von der
 * Messenger-Sprachaufnahme und dem Profil-Mikrofontest genutzt.
 */

export interface AudioSettings {
  noiseSuppression: boolean
  echoCancellation: boolean
  autoGainControl: boolean
  preferredMicId: string | null
}

const STORAGE_KEYS = {
  noiseSuppression: 'msm_audio_noise_suppression',
  echoCancellation: 'msm_audio_echo_cancellation',
  autoGainControl: 'msm_audio_auto_gain',
  preferredMicId: 'msm_preferred_mic_id',
} as const

export function getAudioSettings(): AudioSettings {
  try {
    const micId = localStorage.getItem(STORAGE_KEYS.preferredMicId)
    const noiseSuppression = localStorage.getItem(STORAGE_KEYS.noiseSuppression) !== 'false'
    const echoCancellation = localStorage.getItem(STORAGE_KEYS.echoCancellation) !== 'false'
    const autoGainControl = localStorage.getItem(STORAGE_KEYS.autoGainControl) !== 'false'

    return {
      noiseSuppression,
      echoCancellation,
      autoGainControl,
      preferredMicId: micId && micId.trim() ? micId.trim() : null,
    }
  } catch {
    return {
      noiseSuppression: true,
      echoCancellation: true,
      autoGainControl: true,
      preferredMicId: null,
    }
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
