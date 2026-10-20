/**
 * Zentraler Update-Store für Pontis (Browser).
 *
 * Unter Windows bereitet Rust das Update still im Hintergrund vor
 * (`updater_core::desktop`); sichtbar wird nur das fertige Paket, dann fordert
 * das `UpdateBadge` zum Neustart auf. Die Ladephase bleibt unsichtbar, ein
 * Fehler erscheint am Badge und lässt sich von dort wiederholen. Unter Android
 * meldet sich die Verfügbarkeit als Meldung (`aktualisierung.ts`).
 */
import { create } from 'zustand'
import { listen } from '@tauri-apps/api/event'
import { invoke } from '@tauri-apps/api/core'
import { istTauri } from './nativ'

export type UpdatePhase = 'verfuegbar' | 'laedt' | 'bereit' | 'installiert_android' | 'fehler'

export interface UpdateStatusEvent {
  status: 'laedt' | 'bereit' | 'installiert_android' | 'fehler'
  prozent?: number
  fehler?: string
}

interface UpdateStoreZustand {
  /** Nur ein fertig vorbereitetes Update oder ein Fehler werden gezeigt. */
  sichtbar: boolean
  neueVersion: string | null
  phase: UpdatePhase
  prozent: number
  fehlerText: string | null
  initialisiert: boolean
  installieren: () => Promise<void>
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

    try {
      // Eine nur verfügbare Version wird nicht beworben: Rust lädt sie still.
      void listen<{ version: string; notizen?: string }>('updater:verfuegbar', (event) => {
        set({
          neueVersion: event.payload.version,
          phase: 'verfuegbar',
          prozent: 0,
          fehlerText: null,
          sichtbar: false,
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
            phase: 'laedt',
            prozent: event.payload.prozent ?? 0,
            fehlerText: null,
            sichtbar: false,
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
            phase: 'installiert_android',
            fehlerText: null,
            sichtbar: false,
          })
        } else if (event.payload.status === 'fehler') {
          set({
            sichtbar: true,
            phase: 'fehler',
            fehlerText: event.payload.fehler ?? null,
          })
        }
      })
    } catch {
      // Ignoriert in Testumgebungen ohne vollständigen Webview-Eventkanal
    }
  },

  installieren: async () => {
    if (!istTauri()) return
    // „bereit“ heißt: nur noch neu starten. Nach einem Fehler lädt der Klick
    // erneut; unter Android startet `update_herunterladen` die APK-Installation.
    const bereit = get().phase === 'bereit'
    set({ phase: 'laedt', prozent: 0, fehlerText: null, sichtbar: false })
    try {
      await invoke(bereit ? 'update_installieren' : 'update_herunterladen')
    } catch (e) {
      set({
        sichtbar: true,
        phase: 'fehler',
        fehlerText: e instanceof Error ? e.message : String(e),
      })
    }
  },
}))
