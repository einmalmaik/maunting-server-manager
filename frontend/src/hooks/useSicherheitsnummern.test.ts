/**
 * Sicherheitsnummern: frisch, je Gerät, und nie die des falschen Kontakts.
 *
 * Wer Nummern vergleicht, verlässt sich darauf, dass sie zu genau dem Kontakt
 * gehören, dessen Namen der Dialog nennt. Eine späte Antwort für den vorigen
 * Kontakt darf deshalb nichts mehr anzeigen.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import '@/i18n'

const { geraeteVon, sicherheitsnummer } = vi.hoisted(() => ({
  geraeteVon: vi.fn(),
  sicherheitsnummer: vi.fn(),
}))
vi.mock('@/services/e2eeGeraet', () => ({ geraeteVon, sicherheitsnummer }))

import { useSicherheitsnummern } from './useSicherheitsnummern'

beforeEach(() => {
  geraeteVon.mockReset()
  sicherheitsnummer.mockReset().mockImplementation(async (k: string) => `nr-${k}`)
})

describe('useSicherheitsnummern', () => {
  it('lädt erst, wenn der Dialog offen ist, und gibt einem Gerät ohne Schlüssel keine Nummer', async () => {
    geraeteVon.mockResolvedValue([
      { device_id: 'a', label: 'Laptop', public_key: 'k1' },
      { device_id: 'b', label: '', public_key: null },
    ])
    const { result } = renderHook(() => useSicherheitsnummern(2))
    expect(geraeteVon).not.toHaveBeenCalled()

    act(() => result.current.setOffen(true))
    await waitFor(() => expect(result.current.laedt).toBe(false))
    expect(geraeteVon).toHaveBeenCalledWith(2)
    expect(result.current.geraete).toEqual([
      { id: 'a', label: 'Laptop', number: 'nr-k1' },
      { id: 'b', label: expect.any(String), number: '' },
    ])

    act(() => result.current.setOffen(false))
    expect(result.current.geraete).toEqual([])
  })

  it('zeigt nach einem Kontaktwechsel keine späte Antwort des vorigen Kontakts', async () => {
    let alt!: (v: unknown) => void
    geraeteVon.mockImplementation((id: number) =>
      id === 2 ? new Promise((r) => (alt = r)) : Promise.resolve([{ device_id: 'neu', label: 'X', public_key: 'k3' }]),
    )
    const { result, rerender } = renderHook(({ peer }) => useSicherheitsnummern(peer), {
      initialProps: { peer: 2 },
    })
    act(() => result.current.setOffen(true))
    rerender({ peer: 3 })
    await waitFor(() => expect(result.current.geraete).toHaveLength(1))

    await act(async () => {
      alt([{ device_id: 'alt', label: 'Y', public_key: 'k2' }])
    })
    expect(result.current.geraete).toEqual([{ id: 'neu', label: 'X', number: 'nr-k3' }])
  })
})
