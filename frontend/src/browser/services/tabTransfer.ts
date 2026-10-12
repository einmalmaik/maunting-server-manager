/**
 * Tab-Transfer zwischen Fenstern und Prozessen (Zusammenführen / Re-docking).
 */
export interface TabTransferDaten {
  ursprungFensterId: string
  tabId: string
  url: string
  titel: string
  favicon: string | null
  privat: boolean
}

export const TAB_MIME_TYP = 'application/x-msb-tab'

/** Eindeutige Kennung dieses Browserfensters für die Lebensdauer des Prozesses. */
export const FENSTER_ID =
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : 'f-' + Math.random().toString(36).slice(2) + '-' + Date.now()

const AKTIVER_DRAG_SCHLUESSEL = 'msb:aktiver-tab-drag'
let lokalerAktiverDrag: TabTransferDaten | null = null

export function registriereTabDrag(daten: TabTransferDaten): void {
  lokalerAktiverDrag = daten
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(AKTIVER_DRAG_SCHLUESSEL, JSON.stringify({ ...daten, zeit: Date.now() }))
    }
  } catch {}
}

export function beendeTabDrag(): void {
  lokalerAktiverDrag = null
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(AKTIVER_DRAG_SCHLUESSEL)
    }
  } catch {}
}

export function holeAktivenTabDrag(): TabTransferDaten | null {
  if (lokalerAktiverDrag) return lokalerAktiverDrag
  try {
    if (typeof localStorage !== 'undefined') {
      const raw = localStorage.getItem(AKTIVER_DRAG_SCHLUESSEL)
      if (raw) {
        const parsed = JSON.parse(raw) as TabTransferDaten & { zeit: number }
        if (
          parsed &&
          typeof parsed.ursprungFensterId === 'string' &&
          typeof parsed.tabId === 'string' &&
          Date.now() - (parsed.zeit || 0) < 60000
        ) {
          return {
            ursprungFensterId: parsed.ursprungFensterId,
            tabId: parsed.tabId,
            url: parsed.url,
            titel: parsed.titel,
            favicon: parsed.favicon,
            privat: parsed.privat,
          }
        }
      }
    }
  } catch {}
  return null
}

export function packeTabTransfer(daten: TabTransferDaten): string {
  return JSON.stringify(daten)
}

export function entpackeTabTransfer(dataTransfer: DataTransfer | null): TabTransferDaten | null {
  // 1. Primär: Eigener MIME-Typ
  if (dataTransfer) {
    try {
      const raw = dataTransfer.getData(TAB_MIME_TYP)
      if (raw) {
        const parsed = JSON.parse(raw) as TabTransferDaten
        if (parsed && typeof parsed.ursprungFensterId === 'string' && typeof parsed.tabId === 'string') {
          return parsed
        }
      }
    } catch {}

    // 2. Fallback: text/plain als JSON (falls WebView2/OS benutzerdefinierte MIME-Typen filtert)
    try {
      const text = dataTransfer.getData('text/plain')
      if (text && text.startsWith('{') && text.includes('ursprungFensterId')) {
        const parsed = JSON.parse(text) as TabTransferDaten
        if (parsed && typeof parsed.ursprungFensterId === 'string' && typeof parsed.tabId === 'string') {
          return parsed
        }
      }
    } catch {}
  }

  // 3. Shared Active-Drag aus localStorage (garantiert über separate Prozesse hinweg synchron)
  const geteilt = holeAktivenTabDrag()
  if (geteilt) {
    return geteilt
  }

  // 4. Fallback für externe URLs (z.B. aus fremdem Browser / Text-Drop)
  if (dataTransfer) {
    try {
      const text = (dataTransfer.getData('text/uri-list') || dataTransfer.getData('text/plain') || '').trim()
      if (text && (text.startsWith('http://') || text.startsWith('https://') || text.startsWith('msb://'))) {
        return {
          ursprungFensterId: 'extern',
          tabId: 'extern',
          url: text,
          titel: text,
          favicon: null,
          privat: false,
        }
      }
    } catch {}
  }

  return null
}

const SPEICHER_SCHLUESSEL = 'msb:tab-uebernommen'
const KANAL_NAME = 'msb:tab-transfer'

export function sendeTabUebernommen(ursprungFensterId: string, tabId: string): void {
  const nachricht = { ursprungFensterId, tabId, zeit: Date.now() }
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(SPEICHER_SCHLUESSEL, JSON.stringify(nachricht))
    }
  } catch {}

  try {
    if (typeof BroadcastChannel !== 'undefined') {
      const kanal = new BroadcastChannel(KANAL_NAME)
      kanal.postMessage(nachricht)
      kanal.close()
    }
  } catch {}
}

export function istTabUebernommen(tabId: string): boolean {
  try {
    if (typeof localStorage === 'undefined') return false
    const raw = localStorage.getItem(SPEICHER_SCHLUESSEL)
    if (!raw) return false
    const info = JSON.parse(raw) as { ursprungFensterId: string; tabId: string; zeit: number }
    return (
      info.ursprungFensterId === FENSTER_ID &&
      info.tabId === tabId &&
      Date.now() - info.zeit < 5000
    )
  } catch {
    return false
  }
}

export function lauscheTabTransfer(onUebernommen: (info: { ursprungFensterId: string; tabId: string }) => void): () => void {
  const abmelder: (() => void)[] = []

  if (typeof window !== 'undefined') {
    const storageHandler = (e: StorageEvent) => {
      if (e.key === SPEICHER_SCHLUESSEL && e.newValue) {
        try {
          const info = JSON.parse(e.newValue) as { ursprungFensterId: string; tabId: string }
          if (info.ursprungFensterId === FENSTER_ID) {
            onUebernommen(info)
          }
        } catch {}
      }
    }
    window.addEventListener('storage', storageHandler)
    abmelder.push(() => window.removeEventListener('storage', storageHandler))
  }

  if (typeof BroadcastChannel !== 'undefined') {
    try {
      const kanal = new BroadcastChannel(KANAL_NAME)
      kanal.onmessage = (e: MessageEvent) => {
        const info = e.data as { ursprungFensterId: string; tabId: string }
        if (info && info.ursprungFensterId === FENSTER_ID) {
          onUebernommen(info)
        }
      }
      abmelder.push(() => kanal.close())
    } catch {}
  }

  return () => {
    for (const stop of abmelder) stop()
  }
}

export interface TabZiehNachricht {
  art: 'ziehen' | 'ablegen' | 'abbrechen'
  ursprungFensterId: string
  tabId: string
  url: string
  titel: string
  favicon: string | null
  privat: boolean
  screenX: number
  screenY: number
}

const LIVE_KANAL_NAME = 'msb:tab-drag-live'
const LIVE_SPEICHER_SCHLUESSEL = 'msb:tab-drag-live-speicher'

let liveSenderKanal: BroadcastChannel | null = null
function holeLiveSenderKanal(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null
  if (!liveSenderKanal) {
    try {
      liveSenderKanal = new BroadcastChannel(LIVE_KANAL_NAME)
    } catch {}
  }
  return liveSenderKanal
}

export function sendeTabZiehNachricht(nachricht: TabZiehNachricht): void {
  try {
    const kanal = holeLiveSenderKanal()
    kanal?.postMessage(nachricht)
  } catch {}

  try {
    if (typeof localStorage !== 'undefined') {
      if (nachricht.art === 'ziehen') {
        localStorage.setItem(LIVE_SPEICHER_SCHLUESSEL, JSON.stringify({ ...nachricht, zeit: Date.now() }))
      } else {
        localStorage.removeItem(LIVE_SPEICHER_SCHLUESSEL)
      }
    }
  } catch {}
}

export function lauscheTabZiehNachrichten(onNachricht: (msg: TabZiehNachricht) => void): () => void {
  const abmelder: (() => void)[] = []

  if (typeof BroadcastChannel !== 'undefined') {
    try {
      const kanal = new BroadcastChannel(LIVE_KANAL_NAME)
      kanal.onmessage = (e: MessageEvent) => {
        const msg = e.data as TabZiehNachricht
        if (msg && typeof msg.ursprungFensterId === 'string' && typeof msg.art === 'string') {
          onNachricht(msg)
        }
      }
      abmelder.push(() => kanal.close())
    } catch {}
  }

  if (typeof window !== 'undefined') {
    const storageHandler = (e: StorageEvent) => {
      if (e.key === LIVE_SPEICHER_SCHLUESSEL) {
        if (e.newValue) {
          try {
            const parsed = JSON.parse(e.newValue) as TabZiehNachricht
            if (parsed && typeof parsed.ursprungFensterId === 'string') {
              onNachricht(parsed)
            }
          } catch {}
        } else {
          onNachricht({
            art: 'abbrechen',
            ursprungFensterId: '',
            tabId: '',
            url: '',
            titel: '',
            favicon: null,
            privat: false,
            screenX: 0,
            screenY: 0,
          })
        }
      }
    }
    window.addEventListener('storage', storageHandler)
    abmelder.push(() => window.removeEventListener('storage', storageHandler))
  }

  return () => {
    for (const stop of abmelder) stop()
  }
}

