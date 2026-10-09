/**
 * Updates unter Windows: kurz nach dem Start einmal fragen und es per Meldung
 * sagen; installiert wird erst auf Klick. Abgeschaltet und unter Android
 * fragt nichts bei GitHub.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToastStore } from '@/stores/toastStore'

import { NACH_DEM_START_MS, useAktualisierung } from './aktualisierung'
import { useEinstellungenStore } from './einstellungenStore'
import { nativ } from './nativ'

describe('Updates', () => {
  const pruefen = vi.spyOn(nativ, 'updatePruefen')
  const installieren = vi.spyOn(nativ, 'updateInstallieren')

  beforeEach(() => {
    vi.useFakeTimers()
    pruefen.mockReset().mockResolvedValue('5.2.0')
    installieren.mockReset().mockResolvedValue(null)
    useToastStore.setState({ toasts: [] })
    useEinstellungenStore.setState({ updatesSuchen: true })
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
  })

  it('fragt nach dem Start einmal und bietet das Update per Meldung an', async () => {
    renderHook(() => useAktualisierung())
    expect(pruefen).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS)
    })
    expect(pruefen).toHaveBeenCalledTimes(1)
    const [meldung] = useToastStore.getState().toasts
    expect(meldung.message).toBe('Version 5.2.0 ist da.')
    expect(meldung.aktion?.label).toBe('Installieren und neu starten')
    expect(installieren).not.toHaveBeenCalled()
    meldung.aktion?.ausfuehren()
    expect(installieren).toHaveBeenCalledTimes(1)
  })

  it('sagt nichts, wenn es keine neuere Version gibt', async () => {
    pruefen.mockResolvedValue(null)
    renderHook(() => useAktualisierung())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS)
    })
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('fragt nicht, wenn die Suche abgeschaltet ist', async () => {
    useEinstellungenStore.setState({ updatesSuchen: false })
    renderHook(() => useAktualisierung())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS * 2)
    })
    expect(pruefen).not.toHaveBeenCalled()
  })

  it('fragt unter Android nicht', async () => {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36')
    renderHook(() => useAktualisierung())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS * 2)
    })
    expect(pruefen).not.toHaveBeenCalled()
  })
})
