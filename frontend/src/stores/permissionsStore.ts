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
    set({ isLoading: true, error: null })
    try {
      const me = await rbacApi.me()
      set({ me, isLoading: false, error: null })
    } catch {
      set({ me: null, isLoading: false, error: 'PERMISSIONS_LOAD_FAILED' })
    }
  },

  reset: () => set({ me: null, isLoading: false, error: null }),
}))
