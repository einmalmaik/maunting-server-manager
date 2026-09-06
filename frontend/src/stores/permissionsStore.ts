import { create } from 'zustand'
import { rbacApi } from '@/api/rbac'
import { isNetworkOrOfflineError } from '@/lib/networkErrors'
import type { MePermissions } from '@/types/permissions'

const CACHED_PERMISSIONS_KEY = 'msm_cached_permissions'

function loadCachedPermissions(): MePermissions | null {
  try {
    const raw = localStorage.getItem(CACHED_PERMISSIONS_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && Array.isArray(parsed.global_keys)) {
      return parsed as MePermissions
    }
    return null
  } catch {
    return null
  }
}

function saveCachedPermissions(me: MePermissions | null): void {
  try {
    if (me) {
      localStorage.setItem(CACHED_PERMISSIONS_KEY, JSON.stringify(me))
    } else {
      localStorage.removeItem(CACHED_PERMISSIONS_KEY)
    }
  } catch {}
}

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

const initialCachedPermissions = loadCachedPermissions()

/** Zentraler RBAC-Store. Quelle der Wahrheit fuer Frontend-Permission-Checks.
 *
 * Backend prueft jeden Call zusaetzlich — dieser Store entscheidet nur,
 * was im UI angezeigt wird.
 */
export const usePermissionsStore = create<PermissionsState>((set) => ({
  me: initialCachedPermissions,
  isLoading: false,
  error: null,

  refresh: async () => {
    const abfrage = ++letzteAbfrage
    set({ isLoading: true, error: null })
    try {
      const me = await rbacApi.me()
      if (abfrage !== letzteAbfrage) return
      saveCachedPermissions(me)
      set({ me, isLoading: false, error: null })
    } catch (err) {
      if (abfrage !== letzteAbfrage) return
      if (isNetworkOrOfflineError(err)) {
        const cached = loadCachedPermissions()
        set({ me: cached, isLoading: false, error: null })
        return
      }
      set({ me: null, isLoading: false, error: 'PERMISSIONS_LOAD_FAILED' })
    }
  },

  reset: () => {
    letzteAbfrage += 1
    saveCachedPermissions(null)
    set({ me: null, isLoading: false, error: null })
  },
}))
