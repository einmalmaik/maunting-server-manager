import { create } from 'zustand'
import { api, clearCsrfTokenMemory } from '@/api/client'
import { isNetworkOrOfflineError } from '@/lib/networkErrors'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { useNodeStore } from '@/stores/nodeStore'
import { useToastStore } from '@/stores/toastStore'
import { useConfirmStore } from '@/stores/confirmStore'
import { usePromptStore } from '@/stores/promptStore'
import { clearSqlConsoleHistory } from '@/lib/sqlConsoleStorage'
import { useVaultStore } from '@/desktop/vault/vaultStore'
import { clearMemoryKeyStore } from '@/services/e2eeCrypto'
import { clearGeraeteMemory } from '@/services/e2eeGeraet'
import { clearNotesKeyCache } from '@/services/notesCalendarCrypto'
import { leereAntwortSperren, leereGeraeteStand, leereUmzuege } from '@/services/gruppenSchluessel'
import { leereMailboxAbos } from '@/services/mailboxAbo'
import { leereGruppenNamen } from '@/services/gruppenName'
import { leereGespraeche } from '@/services/gespraechsListe'
import { leereMailboxNachweise } from '@/services/mailboxNachweis'
import { leereKlartextSpeicher } from '@/services/klartextSpeicher'
import { kuendigeMailboxPush, leereMailboxPush } from '@/services/mailboxPush'
import { kuendige } from '@/services/pushAbo'
import type { User } from '@/types'

const CACHED_USER_KEY = 'msm_cached_user'

function loadCachedUser(): User | null {
  try {
    const raw = localStorage.getItem(CACHED_USER_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && typeof parsed.id === 'number' && typeof parsed.username === 'string') {
      return parsed as User
    }
    return null
  } catch {
    return null
  }
}

function saveCachedUser(user: User | null): void {
  // Der Geräteschlüssel des Messengers hängt am Konto und wird hier bekannt
  // gegeben. Diese Zeile steht vor dem `try`: ein gesperrter localStorage darf
  // nicht dazu führen, dass `e2eeGeraet` beim vorigen Konto bleibt.
  setzeAngemeldetesKonto(user?.id ?? null)
  try {
    if (user) {
      localStorage.setItem(CACHED_USER_KEY, JSON.stringify(user))
    } else {
      localStorage.removeItem(CACHED_USER_KEY)
    }
  } catch {}
}

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

const initialCachedUser = loadCachedUser()
// Der Start aus dem Zwischenspeicher geht nicht durch `saveCachedUser`, also
// hier. Ohne diese Zeile stünde der Messenger nach einem Neuladen ohne Konto da
// und fände seinen Geräteschlüssel nicht.
setzeAngemeldetesKonto(initialCachedUser?.id ?? null)

export const useAuthStore = create<AuthState>((set, get) => ({
  user: initialCachedUser,
  isLoading: true,
  isAuthenticated: Boolean(initialCachedUser),

  setUser: (user) => {
    saveCachedUser(user)
    set({ user })
  },

  finishLogin: async (user) => {
    saveCachedUser(user)
    set({ user, isAuthenticated: true, isLoading: false })
    await usePermissionsStore.getState().refresh()
  },

  updateUser: (patch) => set((state) => {
    const updated = state.user ? { ...state.user, ...patch } : null
    saveCachedUser(updated)
    return { user: updated }
  }),

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
    saveCachedUser(null)
    clearCsrfTokenMemory()
    // Der Abfrageverlauf der SQL-Konsole liegt im localStorage und überlebt das
    // Abmelden. Auf einem geteilten Rechner läge er sonst im Browser des
    // nächsten Benutzers — deshalb fällt er hier zusammen mit dem CSRF-Speicher.
    clearSqlConsoleHistory()
    usePermissionsStore.getState().reset()
    // Tresor sperren und alle Klartext-Einträge sowie CryptoKeys aus dem RAM entfernen (SEC-05)
    useVaultStore.getState().lock()
    // E2EE In-Memory-Schlüssel aus dem RAM leeren. Der Schlüsselbund in der
    // IndexedDB bleibt wie bisher liegen — er ist an das Gerät gebunden, nicht
    // an die Sitzung, und ein erneutes Anmelden soll nicht wieder nach dem
    // Wiederherstellungsschlüssel fragen.
    clearMemoryKeyStore()
    clearGeraeteMemory()
    clearNotesKeyCache()
    leereUmzuege()
    leereAntwortSperren()
    // Die Besitznachweise der Mailboxen liegen nur im Arbeitsspeicher und sind
    // aus dem Gruppengeheimnis jederzeit nachrechenbar. Hier stehenzulassen
    // hiesse, dem naechsten Menschen an diesem Geraet fertige Nachweise zu
    // hinterlassen.
    leereMailboxNachweise()
    // Und die Abos: was der Strom melden soll, gehoert dem angemeldeten Konto.
    leereMailboxAbos()
    // Die Gruppennamen. Sie liegen versiegelt, aber sie liegen da — und wie
    // eine Gruppe heisst, sagt ueber ihren Besitzer oft mehr als jede einzelne
    // Nachricht darin. Der naechste Mensch an diesem Geraet erbt sie nicht.
    leereGruppenNamen()
    // Und die Gespraechsliste. Seit Stufe 6b fuehrt sie der Client, weil der
    // Server nicht mehr wissen soll, wer mit wem schreibt — dann darf sie auch
    // keinen Abmeldevorgang ueberleben.
    leereGespraeche()
    // Dasselbe fuer die Push-Adresse. Die Zeilen im Panel raeumt `logout()`
    // weg, solange die Sitzung noch gilt; hier faellt nur der gemerkte Stand,
    // damit der naechste Anmelder nicht auf eine Meldung wartet, die diese
    // Datei fuer laengst abgeschickt haelt.
    leereMailboxPush()
    // Und der Lesestand der eigenen Geräte-Mailbox. Er ist je Konto getrennt,
    // aber stehenzulassen hiesse, dem nächsten Konto in diesem Tab zu
    // verschweigen, was vor seiner Anmeldung dort ankam.
    leereGeraeteStand()
    // Entschlüsselte Verläufe, Anhänge und die Suche darüber. Sie lagen bis
    // 09/2026 nach dem Abmelden bis zum Neuladen des Tabs im Speicher.
    leereKlartextSpeicher()
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
    // Vor dem Abmelden, solange die Sitzung noch gilt: sonst lehnt das Panel
    // das Austragen ab und das Gerät bekäme weiter Benachrichtigungen für ein
    // Konto, das sich hier abgemeldet hat.
    //
    // Zweimal, weil es zwei Tabellen sind: `kuendigeMailboxPush` trägt die
    // mailboxgebundenen Zeilen aus, `kuendige` die kontogebundene Adresse. Die
    // ersten kennen kein Konto — sie können deshalb auch nicht mit einem
    // wegfallen, und sie stehenzulassen hiesse, dass dieses Gerät weiter
    // Meldungen über Mailboxen bekommt, deren Schlüssel gerade aus dem
    // Speicher gefallen sind.
    //
    // **In dieser Reihenfolge.** `kuendige` beendet am Ende das Abonnement im
    // Browser, und danach findet `kuendigeMailboxPush` keine Adresse mehr, die
    // es austragen könnte. Andersherum bliebe die Zeile für immer stehen.
    await kuendigeMailboxPush()
    await kuendige()
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
      saveCachedUser(user)
      set({ user, isAuthenticated: true, isLoading: false })
      // Permissions parallel laden — Frontend-Permission-Checks wissen damit Bescheid.
      void usePermissionsStore.getState().refresh()
    } catch (err) {
      // Wurde inzwischen geräumt, ist nichts mehr zu tun: ein zweiter Griff
      // würde nur eine danach begonnene Anmeldung wieder abräumen.
      if (stand !== raeumungen) return
      if (isNetworkOrOfflineError(err)) {
        const cached = loadCachedUser()
        if (cached) {
          set({ user: cached, isAuthenticated: true, isLoading: false })
        } else {
          set({ isLoading: false })
        }
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('msm:network-offline'))
        }
        return
      }
      get().clearSession()
    }
  },
}))
