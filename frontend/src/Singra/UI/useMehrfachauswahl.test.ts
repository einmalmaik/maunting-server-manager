import type { KeyboardEvent } from 'react'
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useMehrfachauswahl } from './useMehrfachauswahl'

const ohne = { ctrlKey: false, metaKey: false, shiftKey: false }
const taste = (key: string, mit: { ctrlKey?: boolean } = {}) =>
  ({ key, ctrlKey: false, metaKey: false, ...mit, preventDefault: vi.fn() }) as unknown as KeyboardEvent

describe('useMehrfachauswahl', () => {
  const ids = ['a', 'b', 'c', 'd']

  it('öffnet ohne Auswahl, schaltet mit Strg um und danach mit jedem Klick', () => {
    const { result } = renderHook(() => useMehrfachauswahl(ids))
    expect(result.current.klick('a', ohne)).toBe(false)
    act(() => void result.current.klick('b', { ...ohne, ctrlKey: true }))
    expect([...result.current.auswahl!]).toEqual(['b'])
    act(() => void result.current.klick('c', ohne))
    expect([...result.current.auswahl!]).toEqual(['b', 'c'])
    act(() => void result.current.klick('b', ohne))
    act(() => void result.current.klick('c', ohne))
    // Wer das Letzte abwählt, beendet die Auswahl.
    expect(result.current.auswahl).toBeNull()
  })

  it('wählt mit Umschalt alles zwischen dem letzten und diesem, in der Reihenfolge der Liste', () => {
    const { result } = renderHook(() => useMehrfachauswahl(ids))
    act(() => result.current.umschalten('d'))
    act(() => void result.current.klick('b', { ...ohne, shiftKey: true }))
    expect([...result.current.auswahl!]).toEqual(['b', 'c', 'd'])
  })

  it('nimmt den Klick nach einem langen Druck nicht als neuen Tipp', () => {
    const { result } = renderHook(() => useMehrfachauswahl(ids))
    act(() => result.current.langdruck('a'))
    expect(result.current.klick('a', ohne)).toBe(true)
    expect([...result.current.auswahl!]).toEqual(['a'])
  })

  it('wählt mit Strg+A alles und hört mit Escape auf', () => {
    const { result } = renderHook(() => useMehrfachauswahl(ids))
    expect(result.current.taste(taste('Escape'))).toBe(false)
    act(() => void result.current.taste(taste('a', { ctrlKey: true })))
    expect(result.current.auswahl?.size).toBe(4)
    act(() => void result.current.taste(taste('Escape')))
    expect(result.current.auswahl).toBeNull()
  })

  it('startet über einen Knopf leer und wählt in einer leeren Liste mit Strg+A nichts', () => {
    const { result, rerender } = renderHook(({ liste }) => useMehrfachauswahl(liste), { initialProps: { liste: [] as string[] } })
    expect(result.current.taste(taste('a', { ctrlKey: true }))).toBe(false)
    expect(result.current.auswahl).toBeNull()
    rerender({ liste: ids })
    act(() => result.current.starten())
    expect(result.current.auswahl?.size).toBe(0)
  })
  it('nimmt nach einem langen Druck den Tipp auf einen anderen Eintrag an', () => {
    // Bis 02.10.2026 verschluckte der Riegel nach dem langen Druck jeden Tipp
    // für 700 ms, auch auf einen anderen Eintrag.
    const { result } = renderHook(() => useMehrfachauswahl(ids))
    act(() => result.current.langdruck('a'))
    act(() => void expect(result.current.klick('b', ohne)).toBe(true))
    expect([...result.current.auswahl!]).toEqual(['a', 'b'])
  })

  it('lässt Strg+A in einem Eingabefeld dem Feld', () => {
    const { result } = renderHook(() => useMehrfachauswahl(ids))
    for (const feld of [document.createElement('input'), document.createElement('textarea')]) {
      const ereignis = { ...taste('a', { ctrlKey: true }), target: feld } as unknown as KeyboardEvent
      act(() => void expect(result.current.taste(ereignis)).toBe(false))
      expect(ereignis.preventDefault).not.toHaveBeenCalled()
      expect(result.current.auswahl).toBeNull()
    }
  })
})
