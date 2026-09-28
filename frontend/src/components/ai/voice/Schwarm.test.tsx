import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Schwarm, schwarmZustand } from './Schwarm'

/**
 * Die Attrappe des nachgeladenen Moduls. Gezeichnet wird in
 * `designDnaSwarm.test.ts`; hier zählt, was die Komponente dem Schwarm sagt
 * und wann — ein Schwarm, der bei jedem Zustandswechsel neu aufgebaut würde,
 * ruckte sichtbar.
 */
const modul = vi.hoisted(() => {
  const schwarm = { update: vi.fn(), pulse: vi.fn(), destroy: vi.fn() }
  return {
    schwarm,
    createSwarm: vi.fn((_element: HTMLElement, _optionen: Record<string, unknown>) => schwarm),
    silben: vi.fn((_sekunden: number) => 0.6),
  }
})

vi.mock('@maunting/design-dna/swarm', () => ({
  createSwarm: modul.createSwarm,
  simulatedSpeech: () => modul.silben,
}))

function optionen() {
  return modul.createSwarm.mock.calls[0][1] as {
    state: string
    earth: { latitude: number; longitude: number } | null
    level: () => number
  }
}

describe('schwarmZustand', () => {
  it.each([
    [{ zustand: 'aus' }, 'off'],
    [{ zustand: 'verbindet' }, 'connecting'],
    [{ zustand: 'bereit' }, 'ready'],
    [{ zustand: 'hoert' }, 'listening'],
    [{ zustand: 'denkt' }, 'thinking'],
    [{ zustand: 'denkt', werkzeugLaeuft: true }, 'working'],
    [{ zustand: 'spricht' }, 'speaking'],
    // Spricht die KI mitten im Werkzeuglauf, spricht sie — das Logo kehrt
    // zurück, sobald sie wieder denkt.
    [{ zustand: 'spricht', werkzeugLaeuft: true }, 'speaking'],
    [{ zustand: 'verbindet', abgelaufen: true }, 'expired'],
    // Die Störung schlägt alles, auch das planmäßige Ende.
    [{ zustand: 'hoert', fehler: 'ai.voice.errors.provider', abgelaufen: true }, 'fault'],
  ] as const)('%o ist %s', (eingabe, form) => {
    expect(schwarmZustand(eingabe)).toBe(form)
  })
})

describe('Schwarm', () => {
  beforeEach(() => {
    modul.createSwarm.mockClear()
    modul.schwarm.update.mockClear()
    modul.schwarm.pulse.mockClear()
    modul.schwarm.destroy.mockClear()
  })

  it('laedt den Schwarm nach und gibt ihm Form, Ort und Pegel', async () => {
    render(<Schwarm zustand="ready" pegel={() => 0.3} ort={{ latitude: 52.52, longitude: 13.4 }} />)

    await waitFor(() => expect(modul.createSwarm).toHaveBeenCalledOnce())
    expect(optionen().state).toBe('ready')
    expect(optionen().earth).toEqual({ latitude: 52.52, longitude: 13.4 })
    expect(optionen().level()).toBe(0.3)
  })

  it('liest den Pegel je Bild neu, statt ihn beim Aufbau festzuhalten', async () => {
    let laut = 0.1
    const { rerender } = render(<Schwarm zustand="listening" pegel={() => laut} />)
    await waitFor(() => expect(modul.createSwarm).toHaveBeenCalledOnce())

    laut = 0.8
    expect(optionen().level()).toBe(0.8)
    // Auch eine neue Pegelquelle kommt an, ohne Neuaufbau.
    rerender(<Schwarm zustand="listening" pegel={() => 0.45} />)
    expect(optionen().level()).toBe(0.45)
    expect(modul.createSwarm).toHaveBeenCalledOnce()
  })

  it('wechselt die Form, ohne den Schwarm neu aufzubauen', async () => {
    const { rerender } = render(<Schwarm zustand="thinking" pegel={() => 0} />)
    await waitFor(() => expect(modul.createSwarm).toHaveBeenCalledOnce())

    rerender(<Schwarm zustand="working" pegel={() => 0} ort={{ latitude: 35.68, longitude: 139.69 }} />)

    expect(modul.schwarm.update).toHaveBeenLastCalledWith({
      state: 'working',
      earth: { latitude: 35.68, longitude: 139.69 },
    })
    expect(modul.createSwarm).toHaveBeenCalledOnce()
    expect(modul.schwarm.destroy).not.toHaveBeenCalled()
  })

  it('schickt je Werkzeugstart einen Lichtring, aber keinen beim Erscheinen', async () => {
    const { rerender } = render(<Schwarm zustand="working" pegel={() => 0} impulse={3} />)
    await waitFor(() => expect(modul.createSwarm).toHaveBeenCalledOnce())
    // Wer den Sprachmodus mitten in einer Sitzung öffnet, sieht keinen Ring
    // für einen Start, der längst vorbei ist.
    expect(modul.schwarm.pulse).not.toHaveBeenCalled()

    rerender(<Schwarm zustand="working" pegel={() => 0} impulse={4} />)
    rerender(<Schwarm zustand="working" pegel={() => 0} impulse={5} />)
    rerender(<Schwarm zustand="speaking" pegel={() => 0} impulse={5} />)

    expect(modul.schwarm.pulse).toHaveBeenCalledTimes(2)
  })

  it('spricht in der Vorfuehrung Silben statt des Mikrofons — nur beim Hoeren und Sprechen', async () => {
    const { rerender } = render(<Schwarm zustand="speaking" pegel={() => 0} vorfuehrung />)
    await waitFor(() => expect(modul.createSwarm).toHaveBeenCalledOnce())

    expect(optionen().level()).toBe(0.6)
    rerender(<Schwarm zustand="thinking" pegel={() => 0} vorfuehrung />)
    expect(optionen().level()).toBe(0)
  })

  it('raeumt beim Verlassen auf', async () => {
    const { unmount } = render(<Schwarm zustand="ready" pegel={() => 0} />)
    await waitFor(() => expect(modul.createSwarm).toHaveBeenCalledOnce())

    unmount()

    expect(modul.schwarm.destroy).toHaveBeenCalledOnce()
  })

  it('baut keinen Schwarm mehr auf, wenn die Ansicht vor dem Nachladen wieder weg ist', async () => {
    const { unmount } = render(<Schwarm zustand="ready" pegel={() => 0} />)
    unmount()

    await new Promise((fertig) => setTimeout(fertig, 20))
    // Sonst liefe eine Zeichenschleife an einem Element, das es nicht mehr gibt.
    expect(modul.createSwarm).not.toHaveBeenCalled()
  })

  it('laesst den Sprachmodus stehen, wenn der Schwarm nicht aufgebaut werden kann', async () => {
    modul.createSwarm.mockImplementationOnce(() => {
      throw new Error('Modul nicht ladbar')
    })
    const { rerender } = render(<Schwarm zustand="ready" pegel={() => 0} />)
    await waitFor(() => expect(modul.createSwarm).toHaveBeenCalledOnce())

    // Keine Ausnahme bis in React: die Fläche bleibt leer, der Rest bedienbar.
    expect(() => rerender(<Schwarm zustand="working" pegel={() => 0} impulse={1} />)).not.toThrow()
    expect(modul.schwarm.update).not.toHaveBeenCalled()
  })

  it('ist fuer Screenreader stumm — den Zustand sagt der Text daneben', () => {
    const { container } = render(<Schwarm zustand="ready" pegel={() => 0} />)

    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true')
  })
})
