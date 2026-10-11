import { renderHook, act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useTabsStore } from './tabsStore'
import { useVaultStore } from '@/desktop/vault/vaultStore'
import { useMessengerSperre } from '@/services/messengerSperre'
import { useTabWechselSperre } from './useTabWechselSperre'

describe('useTabWechselSperre', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    useTabsStore.setState({
      tabs: [
        { id: 'tab-1', url: 'https://eins.example', titel: 'Eins', privat: false, nativDa: true },
        { id: 'tab-2', url: 'https://zwei.example', titel: 'Zwei', privat: false, nativDa: true },
      ],
      aktivId: 'tab-1',
    })
    useVaultStore.setState({
      isUnlocked: true,
      isUnlocking: false,
      lockOnWindowBlur: true,
      lock: vi.fn(),
    })
    useMessengerSperre.setState({
      eingerichtet: true,
      entsperrt: true,
      laeuft: false,
      sperrtBeiFensterwechsel: true,
      sperren: vi.fn(),
    })
  })

  it('sperrt nicht beim initialen Mounten', () => {
    renderHook(() => useTabWechselSperre())
    expect(useVaultStore.getState().lock).not.toHaveBeenCalled()
    expect(useMessengerSperre.getState().sperren).not.toHaveBeenCalled()
  })

  it('sperrt nicht beim erneuten Aktivieren desselben Tabs', () => {
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().aktivieren('tab-1')
    })
    expect(useVaultStore.getState().lock).not.toHaveBeenCalled()
    expect(useMessengerSperre.getState().sperren).not.toHaveBeenCalled()
  })

  it('sperrt nicht beim Öffnen eines Hintergrund-Tabs', () => {
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().neuerTab('https://drei.example', { hintergrund: true })
    })
    expect(useTabsStore.getState().aktivId).toBe('tab-1')
    expect(useVaultStore.getState().lock).not.toHaveBeenCalled()
    expect(useMessengerSperre.getState().sperren).not.toHaveBeenCalled()
  })

  it('sperrt Tresor und Messenger bei echtem Tabwechsel, wenn jeweiliger Schalter aktiv ist', () => {
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().aktivieren('tab-2')
    })
    expect(useVaultStore.getState().lock).toHaveBeenCalledTimes(1)
    expect(useMessengerSperre.getState().sperren).toHaveBeenCalledTimes(1)
  })

  it('respektiert getrennte Schalter: sperrt Tresor nicht, wenn Verlassen-Schalter aus ist', () => {
    useVaultStore.setState({ lockOnWindowBlur: false })
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().aktivieren('tab-2')
    })
    expect(useVaultStore.getState().lock).not.toHaveBeenCalled()
    expect(useMessengerSperre.getState().sperren).toHaveBeenCalledTimes(1)
  })

  it('sperrt Messenger nicht ohne eingerichteten PIN', () => {
    useMessengerSperre.setState({ eingerichtet: false, entsperrt: false })
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().aktivieren('tab-2')
    })
    expect(useVaultStore.getState().lock).toHaveBeenCalledTimes(1)
    expect(useMessengerSperre.getState().sperren).not.toHaveBeenCalled()
  })

  it('sperrt nicht während laufender Entsperrung', () => {
    useVaultStore.setState({ isUnlocking: true })
    useMessengerSperre.setState({ laeuft: true })
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().aktivieren('tab-2')
    })
    expect(useVaultStore.getState().lock).not.toHaveBeenCalled()
    expect(useMessengerSperre.getState().sperren).not.toHaveBeenCalled()
  })

  it('sperrt bei Tabwechsel über Tastenkürzel (wechseln)', () => {
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().wechseln(1)
    })
    expect(useTabsStore.getState().aktivId).toBe('tab-2')
    expect(useVaultStore.getState().lock).toHaveBeenCalledTimes(1)
    expect(useMessengerSperre.getState().sperren).toHaveBeenCalledTimes(1)
  })

  it('sperrt beim Schließen des aktiven Tabs, weil der nächste Tab aktiv wird', () => {
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().schliessen('tab-1')
    })
    expect(useTabsStore.getState().aktivId).toBe('tab-2')
    expect(useVaultStore.getState().lock).toHaveBeenCalledTimes(1)
    expect(useMessengerSperre.getState().sperren).toHaveBeenCalledTimes(1)
  })

  it('sperrt nicht beim Schließen eines Hintergrund-Tabs', () => {
    renderHook(() => useTabWechselSperre())
    act(() => {
      useTabsStore.getState().schliessen('tab-2')
    })
    expect(useTabsStore.getState().aktivId).toBe('tab-1')
    expect(useVaultStore.getState().lock).not.toHaveBeenCalled()
    expect(useMessengerSperre.getState().sperren).not.toHaveBeenCalled()
  })
})
