/**
 * Updates unter Windows und Android: kurz nach dem Start einmal fragen.
 * Windows lädt danach still im Hintergrund (das Badge meldet den Neustart),
 * Android zeigt die Meldung mit Klick, weil dort der System-Dialog installiert.
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToastStore } from '@/stores/toastStore'

import { NACH_DEM_START_MS, useAktualisierung } from './aktualisierung'
import { useEinstellungenStore } from './einstellungenStore'
import { nativ, type UpdateInfo } from './nativ'

const NEUES_UPDATE: UpdateInfo = {
  verfuegbar: true,
  aktuelle_version: '5.1.9',
  neue_version: '5.2.0',
  download_url: null,
  notizen: null,
  ist_android: false,
}

describe('Updates', () => {
  let pruefen: ReturnType<typeof vi.spyOn>
  let herunterladen: ReturnType<typeof vi.spyOn>
  let installieren: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.useFakeTimers()
    pruefen = vi.spyOn(nativ, 'updatePruefen').mockResolvedValue(NEUES_UPDATE)
    herunterladen = vi.spyOn(nativ, 'updateHerunterladen').mockResolvedValue(null)
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

  it('bereitet das Update unter Windows still vor, ohne Meldung und ohne Bestätigung', async () => {
    renderHook(() => useAktualisierung())
    expect(pruefen).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS)
    })
    expect(pruefen).toHaveBeenCalledTimes(1)
    expect(herunterladen).toHaveBeenCalledTimes(1)
    expect(installieren).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('sagt nichts und lädt nichts, wenn es keine neuere Version gibt', async () => {
    pruefen.mockResolvedValue({ ...NEUES_UPDATE, verfuegbar: false, neue_version: null })
    renderHook(() => useAktualisierung())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS)
    })
    expect(herunterladen).not.toHaveBeenCalled()
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('fragt nicht, wenn die Suche abgeschaltet ist', async () => {
    useEinstellungenStore.setState({ updatesSuchen: false })
    renderHook(() => useAktualisierung())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS * 2)
    })
    expect(pruefen).not.toHaveBeenCalled()
    expect(herunterladen).not.toHaveBeenCalled()
  })

  it('bietet das Update unter Android per Meldung an und installiert erst auf Klick', async () => {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36')
    renderHook(() => useAktualisierung())
    expect(pruefen).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NACH_DEM_START_MS)
    })
    expect(pruefen).toHaveBeenCalledTimes(1)
    expect(herunterladen).not.toHaveBeenCalled()
    const [meldung] = useToastStore.getState().toasts
    expect(meldung.message).toBe('Version 5.2.0 ist da.')
    expect(meldung.aktion?.label).toBe('Installieren und neu starten')
    meldung.aktion?.ausfuehren()
    expect(herunterladen).toHaveBeenCalledTimes(1)
    expect(installieren).not.toHaveBeenCalled()
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
