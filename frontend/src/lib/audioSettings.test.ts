/**
 * Die Ablage, aus der Wake-Word, Sprachnachrichten, Anrufe und der
 * Mikrofontest im Profil ihre Geräte holen.
 *
 * Lautsprecher und Verstärkung standen lange nur in Modulvariablen: im Panel
 * waren sie nach jedem Neuladen weg, und die Lautsprecherwahl kam überhaupt
 * nirgends an. Diese Tests halten fest, dass beides überlebt.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  GAIN_MAX,
  GAIN_MIN,
  getAudioSettings,
  getAudioTrackConstraints,
  saveAudioSettings,
} from './audioSettings'

beforeEach(() => {
  localStorage.clear()
})

describe('Vorgaben', () => {
  it('hat die Filterkette an und die Verstärkung neutral', () => {
    // Chromiums Filter sind der Grund, warum ein Anruf ohne Kopfhörer nicht
    // rückkoppelt. Sie dürfen nie stillschweigend aus sein.
    expect(getAudioSettings()).toEqual({
      noiseSuppression: true,
      echoCancellation: true,
      autoGainControl: true,
      preferredMicId: null,
      preferredSpeakerId: null,
      micGain: 1,
    })
  })
})

describe('Geräte', () => {
  it('behält Mikrofon und Lautsprecher über einen Schreib-Lese-Umlauf', () => {
    saveAudioSettings({ preferredMicId: 'mic-1', preferredSpeakerId: 'speaker-2' })
    const gelesen = getAudioSettings()
    expect(gelesen.preferredMicId).toBe('mic-1')
    expect(gelesen.preferredSpeakerId).toBe('speaker-2')
  })

  it('löscht eine Wahl, statt einen leeren Wert zu speichern', () => {
    saveAudioSettings({ preferredSpeakerId: 'speaker-2' })
    saveAudioSettings({ preferredSpeakerId: null })
    expect(getAudioSettings().preferredSpeakerId).toBeNull()
  })

  it('lässt ein nicht übergebenes Feld in Ruhe', () => {
    saveAudioSettings({ preferredMicId: 'mic-1' })
    saveAudioSettings({ noiseSuppression: false })
    expect(getAudioSettings().preferredMicId).toBe('mic-1')
  })
})

describe('Verstärkung', () => {
  it('überlebt einen Umlauf', () => {
    saveAudioSettings({ micGain: 1.5 })
    expect(getAudioSettings().micGain).toBe(1.5)
  })

  it('klemmt beim Speichern auf die Grenzen', () => {
    // Ungeklemmt stünde eine 0 im GainNode — das Mikrofon wäre lautlos, ohne
    // dass irgendwo ein Fehler auftaucht.
    saveAudioSettings({ micGain: 0 })
    expect(getAudioSettings().micGain).toBe(GAIN_MIN)

    saveAudioSettings({ micGain: 99 })
    expect(getAudioSettings().micGain).toBe(GAIN_MAX)
  })

  it('macht aus Unsinn im Speicher eine neutrale Verstärkung', () => {
    localStorage.setItem('msm_audio_mic_gain', 'lautlos')
    expect(getAudioSettings().micGain).toBe(1)
  })
})

describe('getAudioTrackConstraints', () => {
  it('baut daraus die Aufnahmebedingungen für Sprachnachricht und Anruf', () => {
    saveAudioSettings({ preferredMicId: 'mic-9', noiseSuppression: false })
    expect(getAudioTrackConstraints()).toEqual({
      deviceId: { ideal: 'mic-9' },
      echoCancellation: true,
      noiseSuppression: false,
      autoGainControl: true,
      channelCount: 1,
    })
  })

  it('lässt die Gerätewahl weg, wenn keine getroffen wurde', () => {
    expect(getAudioTrackConstraints()).not.toHaveProperty('deviceId')
  })
})
