import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { nativ } = await import('./nativ')
const { useEinstellungenStore } = await import('./einstellungenStore')
const { useDarstellung } = await import('./darstellung')

const setzen = vi.spyOn(nativ, 'tabsDarstellung')

beforeEach(() => {
  setzen.mockReset().mockResolvedValue(undefined)
  useEinstellungenStore.setState({ seitenDunkel: false })
})

describe('Darstellung der Seiten', () => {
  it('schickt den gespeicherten Wunsch beim Start an die WebView', async () => {
    renderHook(() => useDarstellung())

    await waitFor(() => expect(setzen).toHaveBeenCalledWith(false))
  })

  it('schickt jede Änderung, auch wenn die native Seite einmal nicht antwortet', async () => {
    setzen.mockRejectedValueOnce(new Error('WebView zu alt'))

    const { rerender } = renderHook(() => useDarstellung())
    await waitFor(() => expect(setzen).toHaveBeenCalledWith(false))

    useEinstellungenStore.setState({ seitenDunkel: true })
    rerender()
    await waitFor(() => expect(setzen).toHaveBeenCalledWith(true))
  })
})
