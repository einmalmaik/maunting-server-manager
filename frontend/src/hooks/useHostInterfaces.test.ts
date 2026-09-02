import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

const apiMock = vi.fn()
vi.mock('@/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
}))

import { useHostInterfaces } from './useHostInterfaces'

describe('useHostInterfaces', () => {
  beforeEach(() => {
    apiMock.mockReset()
    apiMock.mockResolvedValue({ interfaces: [], default_bind_ip: null })
  })

  it('laedt ohne zweiten Parameter wie bisher', async () => {
    renderHook(() => useHostInterfaces(7))
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith('/nodes/7/interfaces'))
  })

  it('fragt nichts ab, solange der Aufrufer die Liste nicht braucht', async () => {
    const { rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useHostInterfaces(7, enabled),
      { initialProps: { enabled: false } },
    )
    // Auch nach einem weiteren Durchlauf darf keine Anfrage stehen.
    rerender({ enabled: false })
    expect(apiMock).not.toHaveBeenCalled()

    rerender({ enabled: true })
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith('/nodes/7/interfaces'))
    expect(apiMock).toHaveBeenCalledTimes(1)
  })
})
