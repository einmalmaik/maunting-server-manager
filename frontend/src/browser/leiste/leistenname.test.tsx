import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const kurzinfo = vi.fn<(blase: Record<string, unknown> | null) => Promise<null>>(() => Promise.resolve(null))
vi.mock('../services/nativ', () => ({ nativ: { kurzinfo: (b: Record<string, unknown> | null) => kurzinfo(b) } }))

import { namenVerbergen, useLeistenname, VERZOEGERUNG_MS } from './leistenname'

function Symbol({ name }: { name: string }) {
  return (
    <button type="button" aria-label={name} {...useLeistenname(name)}>
      x
    </button>
  )
}

const gesendet = () => kurzinfo.mock.calls.map(([b]) => (b ? String(b.text) : null))
const warten = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('Name der Leistensymbole', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    kurzinfo.mockClear()
    render(
      <>
        <Symbol name="Messenger" />
        <Symbol name="Notizen" />
      </>,
    )
  })
  afterEach(async () => {
    namenVerbergen()
    await warten(0)
    vi.useRealTimers()
  })

  it('kommt mit der Maus erst nach der Wartezeit, bei Berührung nie', async () => {
    fireEvent.pointerEnter(screen.getByLabelText('Notizen'), { pointerType: 'touch' })
    await warten(VERZOEGERUNG_MS * 2)
    expect(gesendet()).toEqual([])

    fireEvent.pointerEnter(screen.getByLabelText('Messenger'), { pointerType: 'mouse' })
    await warten(VERZOEGERUNG_MS - 50)
    expect(gesendet()).toEqual([])
    await warten(60)
    expect(gesendet()).toEqual(['Messenger'])
    expect(kurzinfo.mock.calls[0][0]).toMatchObject({ hintergrund: expect.any(Array), schrift: expect.any(Array), rand: expect.any(Array) })
  })

  it('wandert ohne neue Wartezeit zum nächsten Symbol und geht beim Verlassen', async () => {
    fireEvent.pointerEnter(screen.getByLabelText('Messenger'), { pointerType: 'mouse' })
    await warten(VERZOEGERUNG_MS)
    // Durch die Lücke zwischen zwei Symbolen bleibt sie stehen.
    fireEvent.pointerLeave(screen.getByLabelText('Messenger'), { pointerType: 'mouse' })
    await warten(20)
    fireEvent.pointerEnter(screen.getByLabelText('Notizen'), { pointerType: 'mouse' })
    await warten(0)
    expect(gesendet()).toEqual(['Messenger', 'Notizen'])

    fireEvent.pointerLeave(screen.getByLabelText('Notizen'), { pointerType: 'mouse' })
    await warten(500)
    expect(gesendet()).toEqual(['Messenger', 'Notizen', null])
  })

  it('verlassen vor der Wartezeit zeigt nichts', async () => {
    fireEvent.pointerEnter(screen.getByLabelText('Messenger'), { pointerType: 'mouse' })
    await warten(100)
    fireEvent.pointerLeave(screen.getByLabelText('Messenger'), { pointerType: 'mouse' })
    await warten(VERZOEGERUNG_MS)
    expect(gesendet()).toEqual([])
  })
})
