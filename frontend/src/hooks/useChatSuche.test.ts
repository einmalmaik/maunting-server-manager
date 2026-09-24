/**
 * Die Suche im offenen Chat.
 *
 * `VerlaufSuchleiste` entprellt über `suchen` in einem Effekt. Entstünde
 * `suchen` bei jedem Rendern neu, suchte die Leiste nach jedem Treffer wieder,
 * und jeder Treffer rendert neu.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { sucheImChat } = vi.hoisted(() => ({ sucheImChat: vi.fn() }))
vi.mock('@/services/verlaufSuche', () => ({ sucheImChat }))

import { useChatSuche } from './useChatSuche'

const treffer = (uuid: string) => ({ blindMailboxId: 'mb', clientUuid: uuid }) as never

beforeEach(() => {
  sucheImChat.mockReset().mockResolvedValue({ treffer: [treffer('a'), treffer('b'), treffer('c')], gesperrt: false })
})

describe('useChatSuche', () => {
  it('behält `suchen`, solange die Mailbox dieselbe ist', () => {
    const { result, rerender } = renderHook(
      ({ mb, springe }) => useChatSuche(mb, springe),
      { initialProps: { mb: 'mb', springe: vi.fn() } },
    )
    const vorher = result.current.suchen
    rerender({ mb: 'mb', springe: vi.fn() })
    expect(result.current.suchen).toBe(vorher)
    rerender({ mb: 'andere', springe: vi.fn() })
    expect(result.current.suchen).not.toBe(vorher)
  })

  it('springt zum ersten Treffer und blättert im Kreis', async () => {
    const springe = vi.fn()
    const { result } = renderHook(() => useChatSuche('mb', springe))
    act(() => result.current.suchen('hallo'))
    await waitFor(() => expect(result.current.treffer).toHaveLength(3))
    expect(sucheImChat).toHaveBeenCalledWith('mb', 'hallo')
    expect(springe).toHaveBeenLastCalledWith('a')

    act(() => result.current.blaettere(-1))
    expect(result.current.index).toBe(2)
    expect(springe).toHaveBeenLastCalledWith('c')
    act(() => result.current.blaettere(1))
    expect(result.current.index).toBe(0)
  })

  it('sucht ohne Gespräch oder ohne Text nicht', async () => {
    const ohne = renderHook(() => useChatSuche('', vi.fn()))
    act(() => ohne.result.current.suchen('hallo'))

    const leer = renderHook(() => useChatSuche('mb', vi.fn()))
    act(() => leer.result.current.suchen('hallo'))
    await waitFor(() => expect(leer.result.current.treffer).toHaveLength(3))
    act(() => leer.result.current.suchen('   '))
    expect(leer.result.current.treffer).toHaveLength(0)
    expect(sucheImChat).toHaveBeenCalledTimes(1)
  })

  it('vergisst beim Schliessen die Treffer', async () => {
    const { result } = renderHook(() => useChatSuche('mb', vi.fn()))
    act(() => result.current.oeffne())
    act(() => result.current.suchen('hallo'))
    await waitFor(() => expect(result.current.treffer).toHaveLength(3))
    act(() => result.current.blaettere(1))
    act(() => result.current.schliesse())
    expect(result.current).toMatchObject({ offen: false, treffer: [], index: 0 })
  })
})
