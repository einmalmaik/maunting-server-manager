import { create } from 'zustand'
import { api, clearCsrfTokenMemory } from '@/api/client'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { useNodeStore } from '@/stores/nodeStore'
import { useToastStore } from '@/stores/toastStore'
import { useConfirmStore } from '@/stores/confirmStore'
import { usePromptStore } from '@/stores/promptStore'
import { clearSqlConsoleHistory } from '@/lib/sqlConsoleStorage'
import { useVaultStore } from '@/desktop/vault/vaultStore'
import type { User } from '@/types'

interface AuthState {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  setUser: (user: User | null) => void
  finishLogin: (user: User) => Promise<void>
  updateUser: (patch: Partial<User>) => void
  clearSession: () => void
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

/**
 * Zählt, wie oft eine Sitzung geräumt wurde.
 *
 * Das Räumen selbst war vollständig, aber es kann nicht zurückhalten, was
 * schon unterwegs ist: `/auth/me` läuft beim Start los, und wer während
 * dieser Abfrage abmeldet, bekam ihre Antwort danach in den geräumten
 * Speicher gelegt — samt `isAuthenticated: true`. Die Sitzung sah dann wieder
 * angemeldet aus, obwohl das Cookie schon tot war. `nodeStore` zählt seine
 * Abfragen aus genau diesem Grund; hier fehlte es.
 */
let raeumungen = 0

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isLoading: true,
  isAuthenticated: false,

  setUser: (user) => set({ user }),

  finishLogin: async (user) => {
    set({ user, isAuthenticated: true, isLoading: false })
    await usePermissionsStore.getState().refresh()
  },

  updateUser: (patch) => set((state) => ({
    user: state.user ? { ...state.user, ...patch } : null,
  })),

  /**
   * Räumt den gesamten lokalen Zustand einer Sitzung ab.
   *
   * CLAUDE.md, Abschnitt 4: „Nach einem Logout muss der gesamte lokale
   * Authentifizierungs- und Server-State aus dem Speicher gelöscht sein."
   *
   * Eine Sitzung endet auf mehreren Wegen — bewusstes Abmelden, gescheiterter
   * Token-Refresh in `api()` und `apiStream()`, abgelehntes `/auth/me` beim
   * Start —, und jeder Weg muss denselben leeren Speicher hinterlassen. Genau
   * daran ist die Invariante schon zweimal gescheitert: einmal blieb die
   * Knotenliste stehen, einmal setzte der gescheiterte Refresh nur das Flag.
   * Deshalb steht das Aufräumen hier an einer Stelle, und jeder Weg ruft es —
   * es gibt keine zweite Fassung davon und kein Ende einer Sitzung, das nur
   * `isAuthenticated` umlegt.
   *
   * `isAuthenticated: false` fällt zuletzt und ist zugleich der Griff, der die
   * offenen Verbindungen schließt: die Wache der Route hängt daran, hängt die
   * geschützten Seiten ab, und deren Aufräumen beendet die WebSockets und
   * SSE-Ströme, die dort und nur dort geöffnet wurden. Ein eigenes Register
   * offener Verbindungen bräuchte es dafür nicht.
   */
  clearSession: () => {
    raeumungen += 1
    clearCsrfTokenMemory()
    // Der Abfrageverlauf der SQL-Konsole liegt im localStorage und überlebt das
    // Abmelden. Auf einem geteilten Rechner läge er sonst im Browser des
    // nächsten Benutzers — deshalb fällt er hier zusammen mit dem CSRF-Speicher.
    clearSqlConsoleHistory()
    usePermissionsStore.getState().reset()
    // Tresor sperren und alle Klartext-Einträge sowie CryptoKeys aus dem RAM entfernen (SEC-05)
    useVaultStore.getState().lock()
    // Die Knotenliste hält Name, Adresse und Port des Agenten sowie den
    // TLS-Fingerabdruck. Ohne dieses clear() bliebe sie bis zum nächsten
    // Neuladen der Seite im Speicher des Tabs liegen.
    useNodeStore.getState().clear()
    useToastStore.getState().clearAll()
    // Toasts, Bestätigungs- und Eingabedialoge hängen am Wurzelelement und
    // überleben den Sprung zur Anmeldeseite. Ein offener Dialog zeigte dort
    // sonst weiter „Server prod-eu-1 wirklich löschen?" — und sein Aufrufer
    // wartete bis zum Neuladen auf eine Antwort, die niemand mehr geben kann.
    useConfirmStore.getState().resolve(false)
    usePromptStore.getState().resolve(null)
    set({ user: null, isAuthenticated: false, isLoading: false })
  },

  logout: async () => {
    try {
      await api('/auth/logout', { method: 'POST' })
    } catch {
      // Ignorieren: das Backend hat die Cookies gelöscht, der lokale Zustand
      // fällt gleich darunter unabhängig davon.
    }
    get().clearSession()
  },

  checkAuth: async () => {
    const stand = raeumungen
    set({ isLoading: true })
    try {
      const user = await api<User>('/auth/me')
      // Ist die Sitzung während der Abfrage geräumt worden, gehört ihre
      // Antwort nicht mehr in den Speicher — sie beschriebe ihn sonst neu.
      if (stand !== raeumungen) return
      set({ user, isAuthenticated: true, isLoading: false })
      // Permissions parallel laden — Frontend-Permission-Checks wissen damit Bescheid.
      void usePermissionsStore.getState().refresh()
    } catch {
      // Wurde inzwischen geräumt, ist nichts mehr zu tun: ein zweiter Griff
      // würde nur eine danach begonnene Anmeldung wieder abräumen.
      if (stand !== raeumungen) return
      get().clearSession()
    }
  },
}))
