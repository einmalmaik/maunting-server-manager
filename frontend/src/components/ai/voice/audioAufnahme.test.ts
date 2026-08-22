import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { FakeAudioContext, installFakeAudio } from '@/test/fakeAudio'
import { ABTASTRATE, starteAufnahme } from './audioAufnahme'
import { registriereAudioVerarbeitung } from './audioGeraete'

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
    // Die Verarbeitung ist Modulzustand — zurück auf die Vorgaben, sonst
    // trägt ein Test seine Registrierung in den nächsten.
    registriereAudioVerarbeitung({})
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

  it('haengt das Mikrofon nie an den Lautsprecher', async () => {
    await starteAufnahme(() => undefined)

    // Der Prozessor braucht ein Ziel, sonst laeuft er nicht an — aber der Weg
    // dorthin geht ueber eine Verstaerkung von null. Ohne sie waere das keine
    // leise Panne, sondern eine Rueckkopplung. Zwei Verstaerker: erst die
    // Eingangsverstaerkung (neutral 1), dann der stumme Ausgang (0).
    expect(kontext().verstaerker).toHaveLength(2)
    expect(kontext().verstaerker[0].gain.value).toBe(1)
    expect(kontext().verstaerker[1].gain.value).toBe(0)
  })

  it('uebernimmt die registrierte Verarbeitung in die Anfrage', async () => {
    // Die Desktop-App registriert die Wahl des Benutzers; das Panel laesst
    // alles auf den Vorgaben. Was hier steht, ist genau das, was Chromium an
    // lokaler Verarbeitung faehrt — nichts davon geht ins Netz.
    registriereAudioVerarbeitung({ echo: false, rauschen: false, autogain: true })
    await starteAufnahme(() => undefined)

    expect(audio.letzteAnfrage()).toEqual({
      audio: {
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: true,
      },
    })
  })

  it('legt die Eingangsverstaerkung geklemmt vor den Prozessor', async () => {
    // 99 ist kein sinnvoller Faktor, sondern ein Tippfehler in der Konfig —
    // die Klemme (0,25 bis 4) faengt ihn, bevor er das Signal zerreisst.
    registriereAudioVerarbeitung({ verstaerkung: 99 })
    await starteAufnahme(() => undefined)

    expect(kontext().verstaerker[0].gain.value).toBe(4)
  })

  it('verbindet keinen Prozessor ohne Ausgangskanal', async () => {
    // Genau hier lag der Fehler: `createScriptProcessor(…, 1, 0)` liess sich
    // erzeugen, aber nicht verbinden — Chrome wirft `InvalidAccessError`. Der
    // Fehlschlag kam **nach** der Mikrofonfreigabe und wurde als „kein Zugriff
    // auf das Mikrofon" gemeldet.
    await starteAufnahme(() => undefined)

    expect(kontext().prozessoren[0].ausgangskanaele).toBeGreaterThan(0)
    expect(kontext().prozessoren[0].verbunden).toBe(true)
  })

  it('nennt eine verweigerte Freigabe beim Namen', async () => {
    audio.restore()
    audio = installFakeAudio({ verweigern: true })

    await expect(starteAufnahme(() => undefined)).rejects.toMatchObject({
      name: 'AufnahmeAbbruch',
      grund: 'verweigert',
    })
  })

  it('nennt einen Fehler der Audiokette nicht Mikrofon', async () => {
    // Die Freigabe ist erteilt, die Kette dahinter scheitert. Wer das
    // „Mikrofon" nennt, schickt den Menschen in die Browsereinstellungen, wo
    // nichts zu finden ist.
    const kaputt = installFakeAudio()
    const Kaputter = class extends FakeAudioContext {
      createGain(): never {
        throw new Error('kein Audio')
      }
    }
    ;(globalThis as { AudioContext?: unknown }).AudioContext = Kaputter

    await expect(starteAufnahme(() => undefined)).rejects.toMatchObject({
      name: 'AufnahmeAbbruch',
      grund: 'audio',
    })
    // Und das Mikrofon geht trotzdem zu — sonst bliebe der rote Punkt im Tab
    // stehen fuer eine Aufnahme, die es nicht gibt.
    expect(kaputt.letzterStrom()?.getTracks()[0].gestoppt).toBe(true)
    kaputt.restore()
  })
})
