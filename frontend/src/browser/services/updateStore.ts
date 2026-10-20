/**
 * Zentraler Update-Store für Pontis (Browser).
 *
 * Verwaltet den Status der Hintergrund-Prüfung, den Ladefortschritt
 * und steuert die Anzeige des zentralen Design-DNA `UpdateBadge`.
 */
import { create } from 'zustand'
import { listen } from '@tauri-apps/api/event'
import { invoke } from '@tauri-apps/api/core'
import { istTauri } from './nativ'

export type UpdatePhase = 'verfuegbar' | 'laedt' | 'bereit' | 'installiert_android' | 'fehler'

export interface UpdateInfo {
  verfuegbar: boolean
  aktuelle_version: string
  neue_version: string | null
  download_url: string | null
  notizen: string | null
  ist_android: boolean
}

export interface UpdateStatusEvent {
  status: 'laedt' | 'bereit' | 'installiert_android' | 'fehler'
  prozent?: number
  fehler?: string
}

interface UpdateStoreZustand {
  sichtbar: boolean
  neueVersion: string | null
  phase: UpdatePhase
  prozent: number
  fehlerText: string | null
  initialisiert: boolean
  pruefen: () => Promise<void>
  installieren: () => Promise<void>
  schliessen: () => void
  initialisiereLauscher: () => void
}

export const useUpdateStore = create<UpdateStoreZustand>((set, get) => ({
  sichtbar: false,
  neueVersion: null,
  phase: 'verfuegbar',
  prozent: 0,
  fehlerText: null,
  initialisiert: false,

  initialisiereLauscher: () => {
    if (!istTauri() || get().initialisiert) return
    const internals = (window as unknown as { __TAURI_INTERNALS__?: { transformCallback?: unknown } }).__TAURI_INTERNALS__
    if (!internals || typeof internals.transformCallback !== 'function') return

    set({ initialisiert: true })

    // Auf Hintergrund-Events lauschen
    try {
      void listen<{ version: string; notizen?: string }>('updater:verfuegbar', (event) => {
      set({
        sichtbar: true,
        neueVersion: event.payload.version,
        phase: 'verfuegbar',
        prozent: 0,
        fehlerText: null,
      })
    })

    void listen<{ version: string }>('updater:bereit', (event) => {
      set({
        sichtbar: true,
        neueVersion: event.payload.version,
        phase: 'bereit',
        prozent: 100,
        fehlerText: null,
      })
    })

    void listen<UpdateStatusEvent>('updater:status', (event) => {
      if (event.payload.status === 'laedt') {
        set({
          sichtbar: true,
          phase: 'laedt',
          prozent: event.payload.prozent ?? 0,
          fehlerText: null,
        })
      } else if (event.payload.status === 'bereit') {
        set({
          sichtbar: true,
          phase: 'bereit',
          prozent: 100,
          fehlerText: null,
        })
      } else if (event.payload.status === 'installiert_android') {
        set({
          sichtbar: true,
          phase: 'installiert_android',
          fehlerText: null,
        })
      } else if (event.payload.status === 'fehler') {
        set({
          sichtbar: true,
          phase: 'fehler',
          fehlerText: event.payload.fehler ?? 'Fehler beim Update',
        })
      }
    })
    } catch {
      // Ignoriert in Testumgebungen ohne vollständigen Webview-Eventkanal
    }
  },

  pruefen: async () => {
    if (!istTauri()) return
    try {
      const info = await invoke<UpdateInfo>('update_pruefen')
      if (info && info.verfuegbar && info.neue_version) {
        set({
          sichtbar: true,
          neueVersion: info.neue_version,
          phase: 'verfuegbar',
          prozent: 0,
          fehlerText: null,
        })
      }
    } catch {
      // Stille Behandlung bei Verbindungsproblemen
    }
  },

  installieren: async () => {
    if (!istTauri()) return
    const installieren = get().phase === 'bereit'
    set({ phase: 'laedt', prozent: 0, fehlerText: null })
    try {
      await invoke(installieren ? 'update_installieren' : 'update_herunterladen')
    } catch (e) {
      set({
        phase: 'fehler',
        fehlerText: e instanceof Error ? e.message : String(e),
      })
    }
  },

  schliessen: () => set({ sichtbar: false }),
}))
