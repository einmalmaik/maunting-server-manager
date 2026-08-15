import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { installFakeAudio, type FakeAudioContext } from '@/test/fakeAudio'
import { ABTASTRATE, starteAufnahme } from './audioAufnahme'

let audio: ReturnType<typeof installFakeAudio>

function kontext(): FakeAudioContext {
  return audio.kontexte[0]
}

describe('audioAufnahme', () => {
  beforeEach(() => {
    audio = installFakeAudio()
  })

  afterEach(() => {
    audio.restore()
  })

  it('fragt Mono mit Echounterdrueckung an', async () => {
    await starteAufnahme(() => undefined)

    // Ohne `echoCancellation` hoert die KI sich selbst und antwortet auf sich.
    // Das ist an einem Laptop ohne Kopfhoerer der Normalfall, nicht der Randfall.
    expect(audio.letzteAnfrage()).toEqual({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
  })

  it('laesst den Browser auf 24 kHz umrechnen', async () => {
    await starteAufnahme(() => undefined)

    expect(kontext().sampleRate).toBe(ABTASTRATE)
    expect(ABTASTRATE).toBe(24_000)
  })

  it('schickt jedes Paket als Int16 weiter', async () => {
    const pakete: ArrayBuffer[] = []
    await starteAufnahme((paket) => pakete.push(paket))

    kontext().prozessoren[0].sende(new Float32Array([0, 0.5, -0.5]))

    expect(pakete).toHaveLength(1)
    const werte = new Int16Array(pakete[0])
    expect(werte).toHaveLength(3)
    expect(werte[0]).toBe(0)
    expect(werte[1]).toBe(Math.trunc(0.5 * 0x7fff))
    expect(werte[2]).toBe(Math.trunc(-0.5 * 0x8000))
  })

  it('klemmt uebersteuerte Werte, statt sie ueberlaufen zu lassen', async () => {
    const pakete: ArrayBuffer[] = []
    await starteAufnahme((paket) => pakete.push(paket))

    kontext().prozessoren[0].sende(new Float32Array([1.7, -2.4]))

    // Ohne Klemmung wuerde aus einem lauten Wort ein Knacken: der Wert liefe
    // ueber und kaeme mit umgekehrtem Vorzeichen wieder heraus.
    const werte = new Int16Array(pakete[0])
    expect(werte[0]).toBe(32767)
    expect(werte[1]).toBe(-32768)
  })

  it('schliesst Mikrofonspur und Kontext beim Beenden', async () => {
    const aufnahme = await starteAufnahme(() => undefined)

    aufnahme.beenden()

    // Der rote Punkt im Tab ist das Einzige, woran ein Mensch erkennt, ob sein
    // Mikrofon offen ist. Er verschwindet nur, wenn die Spur wirklich zugeht.
    expect(audio.letzterStrom()?.getTracks()[0].gestoppt).toBe(true)
    expect(kontext().geschlossen).toBe(true)
    expect(kontext().prozessoren[0].verbunden).toBe(false)
  })

  it('sendet nach dem Beenden nichts mehr', async () => {
    const pakete: ArrayBuffer[] = []
    const aufnahme = await starteAufnahme((paket) => pakete.push(paket))
    const prozessor = kontext().prozessoren[0]

    aufnahme.beenden()
    prozessor.sende(new Float32Array([0.1]))

    expect(pakete).toHaveLength(0)
  })

  it('vertraegt mehrfaches Beenden', async () => {
    const aufnahme = await starteAufnahme(() => undefined)

    aufnahme.beenden()

    // Der Hook raeumt an mehreren Stellen auf — beim Schliessen des Sockets,
    // beim Verlassen der Seite, beim Fehlschlag. Ein zweiter Aufruf darf nicht
    // werfen, sonst stirbt der Aufraeumpfad in der Mitte.
    expect(() => aufnahme.beenden()).not.toThrow()
  })

  it('wirft weiter, wenn das Mikrofon verweigert wird', async () => {
    audio.restore()
    audio = installFakeAudio({ verweigern: true })

    // Eine stumme Sitzung, in der niemand weiss warum, ist die schlechtere
    // Antwort. Der Aufrufer soll den Fehlschlag sehen.
    await expect(starteAufnahme(() => undefined)).rejects.toThrow()
  })
})
