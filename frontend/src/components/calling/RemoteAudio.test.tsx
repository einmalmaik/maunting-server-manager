/**
 * Der Regressionsschutz gegen den Fehler, der diese Datei nötig gemacht hat.
 *
 * LiveKit liefert Ton, spielt ihn aber nicht ab: ohne ein `<audio>`-Element pro
 * Spur bleibt ein technisch einwandfreier Anruf still. Genau das war der
 * Zustand — Kamera und Bildschirmfreigabe wurden angehängt, das Mikrofon nie.
 */

import { render, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RemoteAudio } from './RemoteAudio'

vi.mock('@/services/livekitRaum', async () => {
  const echt = await vi.importActual<typeof import('@/services/livekitRaum')>(
    '@/services/livekitRaum',
  )
  return { ...echt, setzeLautsprecher: vi.fn().mockResolvedValue(undefined) }
})

const { RoomEvent, Track, setzeLautsprecher } = await import('@/services/livekitRaum')

type Horcher = (...args: unknown[]) => void

/** Ein Raum, der nur kann, was `RemoteAudio` von ihm benutzt. */
function raumAttrappe(veroeffentlichungen: unknown[] = []) {
  const horcher = new Map<string, Set<Horcher>>()
  return {
    remoteParticipants: new Map([
      ['u7', { trackPublications: new Map(veroeffentlichungen.map((v, i) => [String(i), v])) }],
    ]),
    on(ereignis: string, fn: Horcher) {
      if (!horcher.has(ereignis)) horcher.set(ereignis, new Set())
      horcher.get(ereignis)!.add(fn)
      return this
    },
    off(ereignis: string, fn: Horcher) {
      horcher.get(ereignis)?.delete(fn)
      return this
    },
    feuere(ereignis: string, ...args: unknown[]) {
      horcher.get(ereignis)?.forEach((fn) => fn(...args))
    },
    anzahlHorcher(ereignis: string) {
      return horcher.get(ereignis)?.size ?? 0
    },
  }
}

function spur(kind: Track.Kind) {
  const element = document.createElement('audio')
  return {
    kind,
    element,
    attach: vi.fn(() => element),
    detach: vi.fn(),
  }
}

function veroeffentlichung(trackSid: string, source: Track.Source, track: unknown) {
  return { trackSid, source, track }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('RemoteAudio', () => {
  it('hängt eine entfernte Mikrofonspur an ein Element', () => {
    const raum = raumAttrappe()
    render(<RemoteAudio room={raum as never} />)

    const ton = spur(Track.Kind.Audio)
    raum.feuere(
      RoomEvent.TrackSubscribed,
      ton,
      veroeffentlichung('TR_1', Track.Source.Microphone, ton),
    )

    expect(ton.attach).toHaveBeenCalledTimes(1)
    expect(ton.element.autoplay).toBe(true)
    expect(ton.element.hasAttribute('playsinline')).toBe(true)
    // Ein stummes Element wäre derselbe Fehler mit mehr Code.
    expect(ton.element.muted).toBe(false)
  })

  it('hängt auch den Ton einer Bildschirmfreigabe an', () => {
    const raum = raumAttrappe()
    render(<RemoteAudio room={raum as never} />)

    const ton = spur(Track.Kind.Audio)
    raum.feuere(
      RoomEvent.TrackSubscribed,
      ton,
      veroeffentlichung('TR_2', Track.Source.ScreenShareAudio, ton),
    )

    expect(ton.attach).toHaveBeenCalledTimes(1)
  })

  it('lässt Videospuren in Ruhe', () => {
    // Die hängt die Teilnehmerkachel an. Zweimal angehängt hieße zwei Bilder.
    const raum = raumAttrappe()
    render(<RemoteAudio room={raum as never} />)

    const bild = spur(Track.Kind.Video)
    raum.feuere(
      RoomEvent.TrackSubscribed,
      bild,
      veroeffentlichung('TR_3', Track.Source.Camera, bild),
    )

    expect(bild.attach).not.toHaveBeenCalled()
  })

  it('hängt dieselbe Spur nicht zweimal an', () => {
    const raum = raumAttrappe()
    render(<RemoteAudio room={raum as never} />)

    const ton = spur(Track.Kind.Audio)
    const v = veroeffentlichung('TR_4', Track.Source.Microphone, ton)
    raum.feuere(RoomEvent.TrackSubscribed, ton, v)
    raum.feuere(RoomEvent.TrackSubscribed, ton, v)

    expect(ton.attach).toHaveBeenCalledTimes(1)
  })

  it('löst die Spur beim Abmelden wieder', () => {
    const raum = raumAttrappe()
    render(<RemoteAudio room={raum as never} />)

    const ton = spur(Track.Kind.Audio)
    const v = veroeffentlichung('TR_5', Track.Source.Microphone, ton)
    raum.feuere(RoomEvent.TrackSubscribed, ton, v)
    raum.feuere(RoomEvent.TrackUnsubscribed, ton, v)

    expect(ton.detach).toHaveBeenCalledWith(ton.element)
  })

  it('nimmt beim Aufbau mit, was schon läuft', () => {
    // Beim Neuaufbau des Anruffensters ist das der Normalfall: die Spuren sind
    // längst abonniert, ein Ereignis kommt nicht mehr.
    const ton = spur(Track.Kind.Audio)
    const raum = raumAttrappe([veroeffentlichung('TR_6', Track.Source.Microphone, ton)])
    render(<RemoteAudio room={raum as never} />)

    expect(ton.attach).toHaveBeenCalledTimes(1)
  })

  it('legt die Wiedergabe auf den gewählten Lautsprecher', () => {
    // Erst mit einem angehängten Element hat `setSinkId` überhaupt ein Ziel.
    const raum = raumAttrappe()
    render(<RemoteAudio room={raum as never} />)

    const ton = spur(Track.Kind.Audio)
    raum.feuere(
      RoomEvent.TrackSubscribed,
      ton,
      veroeffentlichung('TR_7', Track.Source.Microphone, ton),
    )

    expect(setzeLautsprecher).toHaveBeenCalledWith(raum)
  })

  it('räumt seine Horcher ab, wenn das Anruffenster geht', () => {
    const raum = raumAttrappe()
    const { unmount } = render(<RemoteAudio room={raum as never} />)
    expect(raum.anzahlHorcher(RoomEvent.TrackSubscribed)).toBe(1)

    unmount()
    expect(raum.anzahlHorcher(RoomEvent.TrackSubscribed)).toBe(0)
    expect(raum.anzahlHorcher(RoomEvent.TrackUnsubscribed)).toBe(0)
  })

  it('kommt ohne Raum klar', () => {
    expect(() => render(<RemoteAudio room={null} />)).not.toThrow()
  })
})
