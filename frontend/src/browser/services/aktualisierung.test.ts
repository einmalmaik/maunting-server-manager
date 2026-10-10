/**
 * Updates unter Windows und Android: kurz nach dem Start einmal fragen und es
 * per Meldung sagen; installiert wird erst auf Klick.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToastStore } from '@/stores/toastStore'

import { NACH_DEM_START_MS, useAktualisierung } from './aktualisierung'
import { useEinstellungenStore } from './einstellungenStore'
import { nativ } from './nativ'

describe('Updates', () => {
  let pruefen: ReturnType<typeof vi.spyOn>
  let installieren: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.useFakeTimers()
    pruefen = vi.spyOn(nativ, 'updatePruefen').mockResolvedValue('5.2.0')
    installieren = vi.spyOn(nativ, 'updateInstallieren').mockResolvedValue(null)
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

  it('fragt auch unter Android und bietet das Update per Meldung an', async () => {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36')
    renderHook(() => useAktualisierung())
    expect(pruefen).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS)
    })
    expect(pruefen).toHaveBeenCalledTimes(1)
    const [meldung] = useToastStore.getState().toasts
    expect(meldung.message).toBe('Version 5.2.0 ist da.')
  })

  it('fragt im Webbrowser ohne Tauri nicht', async () => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    renderHook(() => useAktualisierung())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS * 2)
    })
    expect(pruefen).not.toHaveBeenCalled()
  })
})
