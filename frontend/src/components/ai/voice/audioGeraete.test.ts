import { afterEach, describe, expect, it } from 'vitest'

import { aktuelleVerarbeitung, registriereAudioVerarbeitung } from './audioGeraete'

describe('audioGeraete: Mikrofon-Verarbeitung', () => {
  afterEach(() => {
    // Modulzustand — zurück auf die Vorgaben, sonst erbt der nächste Test.
    registriereAudioVerarbeitung({})
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
})
