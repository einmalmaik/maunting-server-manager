import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { installFakeAudio, type FakeAudioContext } from '@/test/fakeAudio'
import { ABTASTRATE } from './audioAufnahme'
import { Wiedergabe } from './audioWiedergabe'

let audio: ReturnType<typeof installFakeAudio>

/** Ein Tonstueck aus `anzahl` Samples — der Inhalt ist hier egal, die Laenge nicht. */
function stueck(anzahl: number): ArrayBuffer {
  return new Int16Array(anzahl).buffer
}

function kontext(): FakeAudioContext {
  return audio.kontexte[0]
}

describe('Wiedergabe', () => {
  beforeEach(() => {
    audio = installFakeAudio()
  })

  afterEach(() => {
    audio.restore()
  })

  it('oeffnet den Kontext erst beim ersten Ton', () => {
    const wiedergabe = new Wiedergabe()

    // Ein Kontext ohne Nutzergeste bleibt in den meisten Browsern `suspended`.
    // Beim ersten Stueck hat der Mensch laengst auf den Knopf gedrueckt.
    expect(audio.kontexte).toHaveLength(0)

    wiedergabe.spiele(stueck(10))
    expect(audio.kontexte).toHaveLength(1)
    expect(kontext().sampleRate).toBe(ABTASTRATE)
  })

  it('haengt Stuecke luekenlos aneinander', () => {
    const wiedergabe = new Wiedergabe()
    const laenge = ABTASTRATE / 10 // 0,1 Sekunden

    wiedergabe.spiele(stueck(laenge))
    wiedergabe.spiele(stueck(laenge))
    wiedergabe.spiele(stueck(laenge))

    // Das ist der ganze Zweck der mitlaufenden Uhr: jedes Stueck beginnt dort,
    // wo das vorige endet — nicht bei „jetzt", sonst gaebe es bei jedem
    // verspaeteten Paket eine hoerbare Luecke.
    const starts = kontext().quellen.map((quelle) => quelle.startZeit ?? 0)
    expect(starts[1] - starts[0]).toBeCloseTo(0.1, 5)
    expect(starts[2] - starts[1]).toBeCloseTo(0.1, 5)
  })

  it('setzt die Uhr nach einer Pause neu an', () => {
    const wiedergabe = new Wiedergabe()
    const laenge = ABTASTRATE / 10

    wiedergabe.spiele(stueck(laenge))
    // Eine Pause: die Gegenstelle hat zwei Sekunden lang nichts geschickt.
    kontext().currentTime = 2

    wiedergabe.spiele(stueck(laenge))

    // Ohne das Nachfuehren laege der zweite Start in der Vergangenheit und
    // wuerde sofort und ueberlappend abgespielt.
    const zweiter = kontext().quellen[1].startZeit ?? 0
    expect(zweiter).toBeGreaterThan(2)
    expect(zweiter).toBeLessThan(2.2)
  })

  it('stoppt beim Abbrechen alles Laufende', () => {
    const wiedergabe = new Wiedergabe()
    wiedergabe.spiele(stueck(100))
    wiedergabe.spiele(stueck(100))
    expect(wiedergabe.spricht).toBe(true)

    wiedergabe.abbrechen()

    // Ohne das redet die KI noch zehn Sekunden weiter, obwohl der Mensch
    // laengst dazwischengeredet hat.
    expect(kontext().quellen.every((quelle) => quelle.gestoppt)).toBe(true)
    expect(wiedergabe.spricht).toBe(false)
  })

  it('faengt nach dem Abbrechen wieder bei jetzt an', () => {
    const wiedergabe = new Wiedergabe()
    wiedergabe.spiele(stueck(ABTASTRATE)) // eine ganze Sekunde eingeplant
    wiedergabe.abbrechen()

    wiedergabe.spiele(stueck(100))

    // Die verworfene Sekunde darf die Uhr nicht belasten — sonst schwiege die
    // Antwort auf die neue Frage eine Sekunde lang.
    const neuer = kontext().quellen[1].startZeit ?? 0
    expect(neuer).toBeLessThan(0.2)
  })

  it('vertraegt Stuecke, die schon vorbei sind', () => {
    const wiedergabe = new Wiedergabe()
    wiedergabe.spiele(stueck(100))
    const quelle = kontext().quellen[0]
    quelle.stop = () => {
      throw new Error('InvalidStateError')
    }

    // Ein Stueck, das nie lief oder schon vorbei ist, wirft beim Stoppen.
    // Beides ist genau das, was der Abbruch wollte.
    expect(() => wiedergabe.abbrechen()).not.toThrow()
  })

  it('ignoriert leere Rahmen', () => {
    const wiedergabe = new Wiedergabe()

    wiedergabe.spiele(new ArrayBuffer(0))

    expect(audio.kontexte).toHaveLength(0)
  })

  it('schliesst den Kontext am Ende der Sitzung', () => {
    const wiedergabe = new Wiedergabe()
    wiedergabe.spiele(stueck(100))

    wiedergabe.schliessen()

    expect(kontext().geschlossen).toBe(true)
    expect(wiedergabe.spricht).toBe(false)
  })
})
