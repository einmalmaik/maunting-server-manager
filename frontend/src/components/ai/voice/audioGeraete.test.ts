import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { saveAudioSettings } from '@/lib/audioSettings'
import {
  aktuelleVerarbeitung,
  ausgabeGeraetId,
  eingabeGeraetId,
  registriereAudioGeraete,
  registriereAudioVerarbeitung,
} from './audioGeraete'

describe('audioGeraete: Mikrofon-Verarbeitung', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    // Modulzustand — zurück auf die Vorgaben, sonst erbt der nächste Test.
    registriereAudioVerarbeitung({})
    registriereAudioGeraete(null, null)
    localStorage.clear()
  })

  it('startet mit den Vorgaben: alles an, Verstärkung neutral', () => {
    // Das ist der Zustand des Panels, das nie registriert: die Chromium-Kette
    // ist der Grund, warum die Sprachsitzung ohne Kopfhörer funktioniert.
    expect(aktuelleVerarbeitung()).toEqual({
      echo: true,
      rauschen: true,
      autogain: true,
      verstaerkung: 1,
    })
  })

  it('klemmt die Verstärkung auf 0,25 bis 4', () => {
    registriereAudioVerarbeitung({ verstaerkung: 0.01 })
    expect(aktuelleVerarbeitung().verstaerkung).toBe(0.25)

    registriereAudioVerarbeitung({ verstaerkung: 99 })
    expect(aktuelleVerarbeitung().verstaerkung).toBe(4)
  })

  it('macht aus NaN eine neutrale Verstärkung, nie Stille', () => {
    // NaN entstünde aus einer von Hand editierten konfig.json. Ungeklemmt
    // stünde NaN im GainNode — und das Mikrofon wäre lautlos, ohne Fehler.
    registriereAudioVerarbeitung({ verstaerkung: Number.NaN })
    expect(aktuelleVerarbeitung().verstaerkung).toBe(1)
  })

  it('lässt fehlende Felder auf den Vorgaben', () => {
    // Ein `undefined` aus einer alten Konfiguration darf die Vorgabe nicht
    // überschreiben — sonst wäre `echo` plötzlich weder an noch aus.
    registriereAudioVerarbeitung({ echo: undefined, rauschen: false })
    expect(aktuelleVerarbeitung()).toEqual({
      echo: true,
      rauschen: false,
      autogain: true,
      verstaerkung: 1,
    })
  })

  it('nimmt die gespeicherte Verstärkung, solange keine registriert ist', () => {
    // Das ist der Fall des Panels nach einem Neuladen: dort registriert nichts,
    // und ohne diesen Rückgriff stünde die Verstärkung wieder auf 100 %.
    saveAudioSettings({ micGain: 2 })
    expect(aktuelleVerarbeitung().verstaerkung).toBe(2)
  })

  it('lässt eine registrierte Verstärkung vorgehen', () => {
    // Die Desktop-App registriert bei jedem Start ihren Wert aus der
    // Konfiguration. Der gewinnt gegen den Browserspeicher.
    saveAudioSettings({ micGain: 2 })
    registriereAudioVerarbeitung({ verstaerkung: 0.5 })
    expect(aktuelleVerarbeitung().verstaerkung).toBe(0.5)
  })

  it('verliert die gespeicherte Verstärkung nicht beim Umlegen eines Filters', () => {
    // `registriereAudioVerarbeitung({rauschen})` setzt alle Felder neu. Vorher
    // fiel die Verstärkung dabei still auf 1 zurück.
    saveAudioSettings({ micGain: 2 })
    registriereAudioVerarbeitung({ rauschen: false })
    expect(aktuelleVerarbeitung().verstaerkung).toBe(2)
  })
})

describe('audioGeraete: Gerätewahl', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    registriereAudioGeraete(null, null)
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('liefert die gespeicherte Mikrofonkennung direkt', async () => {
    saveAudioSettings({ preferredMicId: 'mic-3' })
    await expect(eingabeGeraetId()).resolves.toBe('mic-3')
  })

  it('liefert die gespeicherte Lautsprecherkennung direkt', async () => {
    // Ohne diese Abkürzung liefe die Wahl des Panels in die Namensauflösung und
    // käme nie an: dort wird gegen `label` verglichen, nicht gegen `deviceId`.
    saveAudioSettings({ preferredSpeakerId: 'speaker-3' })
    const enumerateDevices = vi.fn()
    vi.stubGlobal('navigator', { mediaDevices: { enumerateDevices } })

    await expect(ausgabeGeraetId()).resolves.toBe('speaker-3')
    expect(enumerateDevices).not.toHaveBeenCalled()
  })

  it('löst ohne gespeicherte Kennung den Namen der Desktop-App auf', async () => {
    vi.stubGlobal('navigator', {
      mediaDevices: {
        enumerateDevices: vi
          .fn()
          .mockResolvedValue([
            { kind: 'audiooutput', label: 'Kopfhörer (USB)', deviceId: 'dev-77' },
          ]),
      },
    })
    registriereAudioGeraete(null, 'Kopfhörer (USB)')
    await expect(ausgabeGeraetId()).resolves.toBe('dev-77')
  })

  it('fällt auf den Systemstandard, wenn das Gerät verschwunden ist', async () => {
    // Ein abgezogenes USB-Mikrofon soll den Sprachmodus nicht lahmlegen.
    vi.stubGlobal('navigator', {
      mediaDevices: { enumerateDevices: vi.fn().mockResolvedValue([]) },
    })
    registriereAudioGeraete(null, 'Weg damit')
    await expect(ausgabeGeraetId()).resolves.toBeNull()
  })
})
