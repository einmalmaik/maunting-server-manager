/**
 * Entwürfe: entprellt, und nach dem Senden wirklich weg.
 *
 * Der Fehler, gegen den das hier steht: Die Entprellung wartete noch, als die
 * Nachricht schon verschickt war, und schrieb den gesendeten Text danach als
 * „Entwurf: …" zurück in die Chatliste.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { speichereEntwurf, ladeAlleEntwuerfe } = vi.hoisted(() => ({
  speichereEntwurf: vi.fn(),
  ladeAlleEntwuerfe: vi.fn(),
}))
vi.mock('@/services/messengerLocalStore', () => ({ speichereEntwurf, ladeAlleEntwuerfe }))

import { useEntwuerfe } from './useEntwuerfe'

beforeEach(() => {
  speichereEntwurf.mockReset().mockResolvedValue(undefined)
  ladeAlleEntwuerfe.mockReset().mockResolvedValue({})
})
afterEach(() => {
  vi.useRealTimers()
})

describe('useEntwuerfe', () => {
  it('schreibt entprellt nur den letzten Stand und kürzt die Vorschau', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useEntwuerfe())
    act(() => result.current.merke('mb', 'Hal'))
    act(() => result.current.merke('mb', 'Hallo ' + 'x'.repeat(100)))
    expect(speichereEntwurf).not.toHaveBeenCalled()

    act(() => vi.advanceTimersByTime(600))
    expect(speichereEntwurf).toHaveBeenCalledTimes(1)
    expect(speichereEntwurf.mock.calls[0][1]).toMatch(/^Hallo x{100}$/)
    expect(result.current.vorschau.mb).toHaveLength(80)
  })

  it('lässt nach dem Verwerfen keinen wartenden Entwurf zurückkommen', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useEntwuerfe())
    act(() => result.current.merke('mb', 'gleich gesendet'))
    act(() => result.current.verwirf('mb'))
    act(() => vi.advanceTimersByTime(1000))

    expect(speichereEntwurf.mock.calls).toEqual([['mb', '']])
    expect(result.current.vorschau.mb).toBeUndefined()
  })

  it('sichert sofort, wenn die Seite in den Hintergrund geht oder verlassen wird', () => {
    vi.useFakeTimers()
    const { result, unmount } = renderHook(() => useEntwuerfe())
    act(() => result.current.merke('mb', 'Anruf kommt'))
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(speichereEntwurf).toHaveBeenLastCalledWith('mb', 'Anruf kommt')

    act(() => result.current.merke('mb', 'noch mehr'))
    unmount()
    expect(speichereEntwurf).toHaveBeenLastCalledWith('mb', 'noch mehr')
  })

  it('lädt die Vorschauen beim Öffnen und lässt leere weg', async () => {
    ladeAlleEntwuerfe.mockResolvedValue({ a: '  Text  ', b: '   ' })
    const { result } = renderHook(() => useEntwuerfe())
    await waitFor(() => expect(result.current.vorschau).toEqual({ a: 'Text' }))
  })
})
