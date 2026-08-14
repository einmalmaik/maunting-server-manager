import { create } from 'zustand'
import { rbacApi } from '@/api/rbac'
import type { MePermissions } from '@/types/permissions'

interface PermissionsState {
  me: MePermissions | null
  isLoading: boolean
  error: string | null
  refresh: () => Promise<void>
  reset: () => void
}

/**
 * Zählt die Abfragen, damit eine unterwegs befindliche Antwort nicht in einen
 * Speicher fällt, den inzwischen jemand geräumt hat. `checkAuth()` startet
 * `refresh()` ohne darauf zu warten — wer in dieser Sekunde abmeldet, bekäme
 * die Rechtekarte (Rolle, globale Schlüssel, Serverschlüssel) sonst nach dem
 * `reset()` wieder eingespielt. Dasselbe Muster wie in `nodeStore`.
 */
let letzteAbfrage = 0

/** Zentraler RBAC-Store. Quelle der Wahrheit fuer Frontend-Permission-Checks.
 *
 * Backend prueft jeden Call zusaetzlich \u2014 dieser Store entscheidet nur,
 * was im UI angezeigt wird.
 */
export const usePermissionsStore = create<PermissionsState>((set) => ({
  me: null,
  isLoading: false,
  error: null,

  refresh: async () => {
    const abfrage = ++letzteAbfrage
    set({ isLoading: true, error: null })
    try {
      const me = await rbacApi.me()
      if (abfrage !== letzteAbfrage) return
      set({ me, isLoading: false, error: null })
    } catch {
      if (abfrage !== letzteAbfrage) return
      set({ me: null, isLoading: false, error: 'PERMISSIONS_LOAD_FAILED' })
    }
  },

  reset: () => {
    letzteAbfrage += 1
    set({ me: null, isLoading: false, error: null })
  },
}))
