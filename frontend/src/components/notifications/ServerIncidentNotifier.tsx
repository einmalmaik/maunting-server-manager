/**
 * ServerIncidentNotifier
 *
 * Lauscht periodisch auf ungelöste Server-Vorfälle (Guardian Engine / Server-Ausfälle)
 * sowie fällige Terminerinnerungen und stellt diese als Push-Benachrichtigung auf
 * Windows/Android und als Pop-up-Toast dar.
 *
 * Sicherheitsinvariante:
 * - Benachrichtigungen werden nur ausgelöst, wenn `user.device_notifications` aktiv ist.
 * - Bereits quittierte/gemeldete Vorfälle werden dedupliziert, um Spam zu verhindern.
 */
import { useEffect, useRef } from 'react'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { sendeGeraeteBenachrichtigung, pruefeUndFrageGeraeteBerechtigung } from '@/lib/benachrichtigung'
import { useMessengerNotificationStore, playNotificationChime } from '@/stores/messengerNotificationStore'

interface IncidentAlert {
  id: number
  uuid: string
  server_id: number
  server_name: string
  title: string
  description: string
  type: string
  severity: string
  timestamp: string
}

interface CalendarDueReminder {
  event_id: string
  title: string
  start: string
  location: string
  time_hint: string
  key: string
}

const POLL_INTERVAL_MS = 20_000
const SESSION_INCIDENTS_KEY = 'msm_alerted_incidents'
const SESSION_REMINDERS_KEY = 'msm_alerted_reminders'

function loadSeenSet(storageKey: string): Set<string> {
  try {
    const raw = sessionStorage.getItem(storageKey)
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr)) return new Set(arr)
    }
  } catch {}
  return new Set<string>()
}

function saveSeenSet(storageKey: string, set: Set<string>) {
  try {
    const arr = Array.from(set).slice(-100) // Maximal 100 Einträge im Session-Speicher
    sessionStorage.setItem(storageKey, JSON.stringify(arr))
  } catch {}
}

export function ServerIncidentNotifier() {
  const { user, isAuthenticated } = useAuthStore()
  const seenIncidentsRef = useRef<Set<string>>(loadSeenSet(SESSION_INCIDENTS_KEY))
  const seenRemindersRef = useRef<Set<string>>(loadSeenSet(SESSION_REMINDERS_KEY))
  const isPollingRef = useRef(false)

  useEffect(() => {
    if (!isAuthenticated || !user || user.device_notifications === false) {
      return
    }

    // Bei aktivem Dienst Berechtigungen prüfen & abfragen
    void pruefeUndFrageGeraeteBerechtigung()

    const checkAlerts = async () => {
      if (isPollingRef.current) return
      isPollingRef.current = true

      try {
        // 1. Server-Vorfälle abrufen
        const incidents = await api<IncidentAlert[]>('/system/incident-alerts').catch(() => [])
        if (Array.isArray(incidents)) {
          let updatedIncidents = false
          for (const inc of incidents) {
            if (inc.uuid && !seenIncidentsRef.current.has(inc.uuid)) {
              seenIncidentsRef.current.add(inc.uuid)
              updatedIncidents = true

              // Push-Benachrichtigung (OS Windows / Android)
              void sendeGeraeteBenachrichtigung({
                titel: `Server-Vorfall: ${inc.server_name}`,
                text: `${inc.title} (${inc.type})`,
              })

              // Pop-up Toast im Interface
              toast.error(`⚠️ Vorfall auf ${inc.server_name}: ${inc.title}`)
            }
          }
          if (updatedIncidents) {
            saveSeenSet(SESSION_INCIDENTS_KEY, seenIncidentsRef.current)
          }
        }

        // 2. Fällige Termine (24h / 48h) abrufen
        const reminders = await api<CalendarDueReminder[]>('/calendar/due-reminders').catch(() => [])
        if (Array.isArray(reminders)) {
          let updatedReminders = false
          for (const rem of reminders) {
            if (rem.key && !seenRemindersRef.current.has(rem.key)) {
              seenRemindersRef.current.add(rem.key)
              updatedReminders = true

              // Push-Benachrichtigung (OS Windows / Android)
              void sendeGeraeteBenachrichtigung({
                titel: `Terminerinnerung (${rem.time_hint})`,
                text: `${rem.title} am ${rem.start}`,
              })

              // Pop-up Toast im Interface
              toast.success(`📅 Terminerinnerung (${rem.time_hint}): ${rem.title}`)
            }
          }
          if (updatedReminders) {
            saveSeenSet(SESSION_REMINDERS_KEY, seenRemindersRef.current)
          }
        }
      } catch {
        // Stiller Fehler im Hintergrund
      } finally {
        isPollingRef.current = false
      }
    }

    // Hintergrund-Synchronisierung für Blockierungen und bekannte Mailboxen
    void useMessengerNotificationStore.getState().syncBlockedFromBackend()
    if (user?.id) {
      void useMessengerNotificationStore.getState().syncMailboxDirectoryFromBackend(user.id)
    }

    // Sofortiger initialer Check nach Login
    void checkAlerts()

    const interval = setInterval(() => {
      void checkAlerts()
    }, POLL_INTERVAL_MS)

    // Sofortige Echtzeit-Push-Benachrichtigung für Freundschaftsanfragen & Messenger-Nachrichten
    const handleSyncEvent = (e: Event) => {
      const ce = e as CustomEvent<any>
      const detail = ce.detail
      if (detail?.type === 'friend_request_received') {
        const senderName = detail.from_username || 'Ein Benutzer'
        void sendeGeraeteBenachrichtigung({
          titel: 'Neue Freundschaftsanfrage',
          text: `${senderName} hat dir eine Freundschaftsanfrage gesendet.`,
        })
        toast.success(`👋 Freundschaftsanfrage von ${senderName} erhalten`)
      } else if (detail?.type === 'e2ee_blind_message') {
        const mid = detail.blind_mailbox_id
        if (!mid) return

        // 1. Eigene Nachricht (oder per KI im eigenen Namen versendet) -> Absender nicht selbst benachrichtigen!
        if (detail.sender_user_id && user?.id && detail.sender_user_id === user.id) {
          return
        }

        const store = useMessengerNotificationStore.getState()

        // 2. Nur Mailboxen benachrichtigen, die dem Benutzer auch tatsächlich zugeordnet sind!
        // Niemals Geister-Benachrichtigungen („Messenger“) für fremde Mailboxen unbeteiligter Nutzer auslösen!
        const meta = store.mailboxDirectory[mid]
        if (!meta) {
          return
        }

        // 3. Stummschaltung und Blockierung prüfen
        if (store.isMuted(mid)) return
        if (meta.userId && store.isBlocked(meta.userId)) {
          return
        }

        // 4. Wenn der Chat im aktuellen Tab/Fenster aktiv geöffnet und fokussiert ist -> kein störendes Banner/Ton
        const isCurrentActive = store.activeMailboxId === mid
        if (isCurrentActive && typeof document !== 'undefined' && document.visibilityState === 'visible') {
          return
        }

        const senderOrChat = meta.name
        const title = `Neue Nachricht: ${senderOrChat}`
        const text = meta.isGroup
          ? `Neue Nachricht in Gruppe „${meta.name}“`
          : `Du hast eine neue Nachricht von ${senderOrChat} erhalten.`

        // Ungelesen-Zähler im Store erhöhen
        store.incrementUnread(mid)

        // Nur akustisch signalisieren und benachrichtigen, wenn Gerätebenachrichtigung aktiv ist
        if (user?.device_notifications !== false) {
          playNotificationChime()
          toast.success(meta.isGroup ? `💬 Neue Nachricht in „${meta.name}“` : `💬 Neue Nachricht von ${senderOrChat}`)
          void sendeGeraeteBenachrichtigung({
            titel: title,
            text: text,
          })
        }
      }
    }

    window.addEventListener('msm:sync-event', handleSyncEvent)

    return () => {
      clearInterval(interval)
      window.removeEventListener('msm:sync-event', handleSyncEvent)
    }
  }, [isAuthenticated, user?.id, user?.device_notifications])

  return null
}
