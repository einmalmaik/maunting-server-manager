import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

export function isTauriEnv(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export async function nativeTabErstellen(url?: string, inkognito = false): Promise<any> {
  if (!isTauriEnv()) return null
  try {
    return await invoke('tab_erstellen', { url: url || 'about:blank', inkognito })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_erstellen:', err)
    return null
  }
}

export async function nativeTabAktivieren(tabId: string): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('tab_aktivieren', { tabId })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_aktivieren:', err)
  }
}

export async function nativeTabSchliessen(tabId: string): Promise<string | null> {
  if (!isTauriEnv()) return null
  try {
    return await invoke<string | null>('tab_schliessen', { tabId })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_schliessen:', err)
    return null
  }
}

export async function nativeTabNavigieren(tabId: string, url: string): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('tab_navigieren', { tabId, url })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_navigieren:', err)
  }
}

export async function nativeTabZurueck(tabId: string): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('tab_zurueck', { tabId })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_zurueck:', err)
  }
}

export async function nativeTabVorwaerts(tabId: string): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('tab_vorwaerts', { tabId })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_vorwaerts:', err)
  }
}

export async function nativeTabNeuLaden(tabId: string): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('tab_neu_laden', { tabId })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_neu_laden:', err)
  }
}

export async function nativeTabBoundsAnpassen(
  x: number,
  y: number,
  breite: number,
  hoehe: number
): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('tab_bounds_anpassen', { x, y, breite, hoehe })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_bounds_anpassen:', err)
  }
}

export async function nativeTabSichtbarkeitSetzen(sichtbar: boolean): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('tab_sichtbarkeit_setzen', { sichtbar })
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei tab_sichtbarkeit_setzen:', err)
  }
}

export async function nativeHauptfensterFokussieren(): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('hauptfenster_fokussieren')
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei hauptfenster_fokussieren:', err)
  }
}

export async function nativeFensterSchliessen(): Promise<void> {
  if (!isTauriEnv()) {
    window.close()
    return
  }
  try {
    await invoke('fenster_schliessen')
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei fenster_schliessen:', err)
  }
}

export async function nativeFensterMinimieren(): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('fenster_minimieren')
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei fenster_minimieren:', err)
  }
}

export async function nativeFensterMaximierenUmschalten(): Promise<void> {
  if (!isTauriEnv()) return
  try {
    await invoke('fenster_maximieren_umschalten')
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei fenster_maximieren_umschalten:', err)
  }
}

export async function nativeAdblockStatus(): Promise<{
  geblockte_anzeigen: number
  geblockte_tracker: number
  gesamt_geblockt: number
  adblock_aktiv: boolean
} | null> {
  if (!isTauriEnv()) return null
  try {
    return await invoke('adblock_status')
  } catch (err) {
    console.warn('[MSB Bridge] Fehler bei adblock_status:', err)
    return null
  }
}

export async function setupTauriListeners(callbacks: {
  onAdblockEvent?: (event: { typ: string; url: string; tab_id: string }) => void
  onTabNavigated?: (event: { id: string; url: string }) => void
  onPageLoading?: (event: { id: string; url: string }) => void
  onPageLoaded?: (event: { id: string; url: string }) => void
  onTabsChanged?: () => void
}): Promise<() => void> {
  if (!isTauriEnv()) return () => {}

  const unlistenFns: UnlistenFn[] = []

  if (callbacks.onAdblockEvent) {
    const un = await listen<{ typ: string; url: string; tab_id: string }>(
      'msb:adblock_ereignis',
      (e) => callbacks.onAdblockEvent?.(e.payload)
    )
    unlistenFns.push(un)
  }

  if (callbacks.onTabNavigated) {
    const un = await listen<{ id: string; url: string }>(
      'msb:tab_navigiert',
      (e) => callbacks.onTabNavigated?.(e.payload)
    )
    unlistenFns.push(un)
  }

  if (callbacks.onPageLoading) {
    const un = await listen<{ id: string; url: string }>(
      'msb:tab_laedt',
      (e) => callbacks.onPageLoading?.(e.payload)
    )
    unlistenFns.push(un)
  }

  if (callbacks.onPageLoaded) {
    const un = await listen<{ id: string; url: string }>(
      'msb:tab_geladen',
      (e) => callbacks.onPageLoaded?.(e.payload)
    )
    unlistenFns.push(un)
  }

  if (callbacks.onTabsChanged) {
    const un = await listen('msb:tabs_geaendert', () => callbacks.onTabsChanged?.())
    unlistenFns.push(un)
  }

  return () => {
    unlistenFns.forEach((fn) => fn())
  }
}
