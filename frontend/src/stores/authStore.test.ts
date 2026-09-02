import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useAuthStore } from './authStore'
import { usePermissionsStore } from './permissionsStore'
import { useNodeStore } from './nodeStore'
import { useToastStore } from './toastStore'
import { useConfirmStore } from './confirmStore'
import { usePromptStore } from './promptStore'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  clearCsrfTokenMemory: vi.fn(),
}))

describe('authStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
    useNodeStore.setState({ nodes: [], total: 0, page: 1, loading: false, error: null })
    useToastStore.setState({ toasts: [] })
    useConfirmStore.setState({ pending: null })
    usePromptStore.setState({ pending: null })
    localStorage.clear()
    vi.mocked(client.api).mockReset()
    vi.mocked(client.clearCsrfTokenMemory).mockClear()
  })

  describe('initial state', () => {
    it('should not be authenticated initially', () => {
      const state = useAuthStore.getState()
      expect(state.isAuthenticated).toBe(false)
      expect(state.user).toBeNull()
      expect(state.isLoading).toBe(true)
    })

    it('should NOT read token from localStorage', () => {
      // Verify no localStorage access — store has no token field at all
      const state = useAuthStore.getState()
      expect(state).not.toHaveProperty('token')
    })
  })

  describe('checkAuth', () => {
    it('should authenticate on successful /auth/me', async () => {
      const mockUser = { id: 1, username: 'test', is_owner: true }
      vi.mocked(client.api)
        .mockResolvedValueOnce(mockUser)
        .mockResolvedValueOnce({
          is_owner: true,
          role_id: null,
          role_name: null,
          global_keys: [],
          server_keys: {},
        })

      const store = useAuthStore.getState()
      await store.checkAuth()

      expect(useAuthStore.getState().isAuthenticated).toBe(true)
      expect(useAuthStore.getState().user).toEqual(mockUser)
      expect(useAuthStore.getState().isLoading).toBe(false)
    })

    it('should set isAuthenticated=false on failed /auth/me', async () => {
      vi.mocked(client.api).mockRejectedValueOnce(new Error('Unauthorized'))
      useNodeStore.setState({ nodes: [{ id: 7, name: 'node-eu' } as any], total: 1 })

      const store = useAuthStore.getState()
      await store.checkAuth()

      expect(useAuthStore.getState().isAuthenticated).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
      expect(useAuthStore.getState().isLoading).toBe(false)
      expect(useNodeStore.getState().nodes).toEqual([])
    })
  })

  describe('logout', () => {
    it('should call /auth/logout and clear state', async () => {
      vi.mocked(client.api).mockResolvedValueOnce({})

      useAuthStore.setState({
        user: { id: 1, username: 'test', is_owner: true } as any,
        isAuthenticated: true,
      })

      await useAuthStore.getState().logout()

      expect(client.api).toHaveBeenCalledWith('/auth/logout', { method: 'POST' })
      expect(useAuthStore.getState().isAuthenticated).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('should clear state even if /auth/logout fails', async () => {
      vi.mocked(client.api).mockRejectedValueOnce(new Error('Network error'))

      useAuthStore.setState({
        user: { id: 1, username: 'test', is_owner: true } as any,
        isAuthenticated: true,
      })

      await useAuthStore.getState().logout()

      expect(useAuthStore.getState().isAuthenticated).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
    })
  })

  /**
   * CLAUDE.md, Abschnitt 4: „Nach einem Logout muss der gesamte lokale
   * Authentifizierungs- und Server-State aus dem Speicher gelöscht sein."
   *
   * Diese Zusage ist zweimal gebrochen worden — einmal blieb die Knotenliste
   * mit ihren Agentenadressen stehen, einmal legte der gescheiterte Refresh nur
   * das Flag um. Beide Male fiel es niemandem auf, weil kein Test die Zusage im
   * Ganzen prüfte, sondern immer nur das eine Feld, um das es gerade ging.
   * Hier steht sie im Ganzen: eine Erwartung, gegen jeden Weg, auf dem eine
   * Sitzung endet.
   */
  describe('Invariante: nach dem Ende einer Sitzung ist der Speicher leer', () => {
    const SQL_VERLAUF = 'msm_sql:history:1:server-7'

    function speicherFuellen() {
      useAuthStore.setState({
        user: { id: 1, username: 'admin', is_owner: true } as any,
        isAuthenticated: true,
        isLoading: false,
      })
      usePermissionsStore.setState({
        me: {
          is_owner: true,
          role_id: 3,
          role_name: 'Betreiber',
          global_keys: ['server.config.write'],
          server_keys: { '7': ['server.console.read'] },
        } as any,
        isLoading: false,
        error: null,
      })
      useNodeStore.setState({
        nodes: [{ id: 7, name: 'node-eu', host: 'https://10.0.0.7:8080' } as any],
        total: 1,
      })
      useToastStore.setState({
        toasts: [{ id: 1, message: 'Server prod-eu-1 gestoppt', type: 'error' }],
      })
      localStorage.setItem(SQL_VERLAUF, JSON.stringify(['SELECT * FROM users']))
    }

    function speicherIstLeer() {
      const auth = useAuthStore.getState()
      expect(auth.isAuthenticated).toBe(false)
      expect(auth.user).toBeNull()
      expect(auth.isLoading).toBe(false)
      expect(usePermissionsStore.getState().me).toBeNull()
      expect(useNodeStore.getState().nodes).toEqual([])
      expect(useNodeStore.getState().total).toBe(0)
      expect(useToastStore.getState().toasts).toEqual([])
      expect(useConfirmStore.getState().pending).toBeNull()
      expect(usePromptStore.getState().pending).toBeNull()
      expect(localStorage.getItem(SQL_VERLAUF)).toBeNull()
      expect(client.clearCsrfTokenMemory).toHaveBeenCalled()
    }

    it('nach dem bewussten Abmelden', async () => {
      vi.mocked(client.api).mockResolvedValueOnce({})
      speicherFuellen()

      await useAuthStore.getState().logout()

      speicherIstLeer()
    })

    it('nach dem Abmelden, auch wenn das Backend nicht mehr antwortet', async () => {
      vi.mocked(client.api).mockRejectedValueOnce(new Error('Network error'))
      speicherFuellen()

      await useAuthStore.getState().logout()

      speicherIstLeer()
    })

    it('nach abgelehntem /auth/me — der Weg des abgelaufenen Refresh und der 401', async () => {
      // `api()` räumt bei gescheitertem Refresh selbst auf und wirft dann
      // SESSION_EXPIRED; checkAuth fängt den Wurf und räumt ein zweites Mal.
      // Geprüft wird hier der zweite Griff: er allein muss schon genügen.
      vi.mocked(client.api).mockRejectedValueOnce(new Error('SESSION_EXPIRED'))
      speicherFuellen()

      await useAuthStore.getState().checkAuth()

      speicherIstLeer()
    })

    it('direkt über clearSession — derselbe Weg, den der API-Client geht', () => {
      speicherFuellen()

      useAuthStore.getState().clearSession()

      speicherIstLeer()
    })

    it('gibt offene Dialoge frei, statt sie über der Anmeldeseite stehen zu lassen', async () => {
      vi.mocked(client.api).mockResolvedValueOnce({})
      const frage = useConfirmStore.getState().request({ message: 'Server prod-eu-1 löschen?' })
      const eingabe = usePromptStore.getState().request({ message: 'Name des Servers' })

      await useAuthStore.getState().logout()

      await expect(frage).resolves.toBe(false)
      await expect(eingabe).resolves.toBeNull()
      expect(useConfirmStore.getState().pending).toBeNull()
      expect(usePromptStore.getState().pending).toBeNull()
    })

    /**
     * Der dritte mögliche Bruch: nicht ein vergessener Store, sondern eine
     * Antwort, die zu spät kommt. Beim Start laufen `/auth/me` und
     * `/permissions/me` los; wer in dieser Sekunde abmeldet, räumt einen
     * Speicher, in den die beiden Antworten danach wieder hineinschreiben.
     * `nodeStore` zählt seine Abfragen genau deshalb schon — hier fehlte es.
     */
    it('eine noch offene /auth/me-Antwort weckt die geräumte Sitzung nicht wieder', async () => {
      let meFreigeben: (user: unknown) => void = () => {}
      vi.mocked(client.api)
        .mockImplementationOnce(() => new Promise((resolve) => { meFreigeben = resolve }))
        .mockResolvedValueOnce({})

      speicherFuellen()
      const laufendeAbfrage = useAuthStore.getState().checkAuth()

      await useAuthStore.getState().logout()
      meFreigeben({ id: 1, username: 'admin', is_owner: true })
      await laufendeAbfrage

      speicherIstLeer()
    })

    it('eine noch offene Rechteabfrage füllt die geräumte Sitzung nicht wieder', async () => {
      let rechteFreigeben: (me: unknown) => void = () => {}
      vi.mocked(client.api)
        .mockResolvedValueOnce({ id: 1, username: 'admin', is_owner: true })
        .mockImplementationOnce(() => new Promise((resolve) => { rechteFreigeben = resolve }))
        .mockResolvedValueOnce({})

      await useAuthStore.getState().checkAuth()
      await useAuthStore.getState().logout()

      rechteFreigeben({
        is_owner: true,
        role_id: 3,
        role_name: 'Betreiber',
        global_keys: ['server.delete'],
        server_keys: { '7': ['server.console.read'] },
      })
      await new Promise((resolve) => setTimeout(resolve, 0))

      expect(usePermissionsStore.getState().me).toBeNull()
      expect(useAuthStore.getState().isAuthenticated).toBe(false)
    })

    it('bietet keinen halben Weg an, der nur das Flag umlegt', () => {
      // Der zweite Bruch der Invariante entstand genau so: `setAuthenticated(false)`
      // sah aus wie ein Sitzungsende, war aber nur ein Flag. Die Aktion gibt es
      // deshalb nicht mehr — wer eine Sitzung beendet, ruft `clearSession()`.
      const state = useAuthStore.getState()
      expect(state).not.toHaveProperty('setAuthenticated')
      expect(typeof state.clearSession).toBe('function')
    })
  })

  describe('finishLogin', () => {
    it('sets auth state and loads permissions for route guards', async () => {
      const mockUser = { id: 1, username: 'test', is_owner: true }
      const mockPermissions = {
        is_owner: true,
        role_id: null,
        role_name: null,
        global_keys: [],
        server_keys: {},
      }
      vi.mocked(client.api).mockResolvedValueOnce(mockPermissions)

      await useAuthStore.getState().finishLogin(mockUser as any)

      expect(useAuthStore.getState().user).toEqual(mockUser)
      expect(useAuthStore.getState().isAuthenticated).toBe(true)
      expect(useAuthStore.getState().isLoading).toBe(false)
      expect(usePermissionsStore.getState().me).toEqual(mockPermissions)
      expect(client.api).toHaveBeenCalledWith('/permissions/me')
    })
  })

  describe('security invariant: no token in state', () => {
    it('should never expose token in store', () => {
      const state = useAuthStore.getState()
      const keys = Object.keys(state)
      expect(keys).not.toContain('token')
      expect(keys).not.toContain('accessToken')
      expect(keys).not.toContain('refreshToken')
    })
  })
})
