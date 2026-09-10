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
import { NotificationService } from '@/services/notificationService'

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

        const store = useMessengerNotificationStore.getState()

        // 1. Multi-Device Read Receipt Sync:
        // Wenn der Benutzer den Chat auf einem anderen Gerät gelesen hat,
        // wird der Zähler auf diesem Gerät unmittelbar zurückgesetzt
        const senderId = detail.sender_user_id ?? detail.sender_id
        if (
          detail.control_type === 'read_receipt' &&
          NotificationService.isOutgoingEcho(senderId, user?.id)
        ) {
          store.markAsRead(mid)
          return
        }

        // 2. Outgoing Echo Prevention:
        // Der Sender darf NIEMALS eine Notification oder einen Unread-Count für seine eigenen
        // Aktionen erhalten (auch nicht bei Aktionen via KI/Worker im Namen des Nutzers).
        if (NotificationService.isOutgoingEcho(senderId, user?.id)) {
          return
        }

        // 3. Interne Steuernachrichten (Read Receipts, Delivery Receipts, Edits) ausschließen
        const isControl = Boolean(
          detail.is_control ||
          (detail.control_type && detail.control_type !== 'message' && detail.control_type !== 'normal')
        )
        if (isControl) {
          return
        }

        // 4. Mailbox-Metadaten abrufen oder bei Bedarf nachsynchronisieren
        let meta = store.mailboxDirectory[mid]
        if (!meta) {
          if (user?.id) {
            void store.syncMailboxDirectoryFromBackend(user.id)
          }
          meta = {
            name: 'Neue Nachricht',
            isGroup: Boolean(detail.is_group),
          }
        }


        // Chat-Fokus und Vordergrund-Erkennung
        const isCurrentActive = store.activeMailboxId === mid
        const isDocVisible = typeof document !== 'undefined' && document.visibilityState === 'visible'
        const isWindowFocused = typeof document !== 'undefined' && (typeof document.hasFocus !== 'function' || document.hasFocus())
        const isChatFocused = isCurrentActive && isDocVisible && isWindowFocused
        const isForeground = isDocVisible && isWindowFocused

        // Strikte Outgoing Echo Prevention & Empfänger-Filterung (recipient_id == current_user && !is_read)
        const shouldNotify = NotificationService.shouldNotify({
          recipientId: detail.recipient_id,
          currentUserId: user?.id,
          isRead: isChatFocused,
          senderUserId: detail.sender_user_id,
          isGroup: Boolean(meta.isGroup),
          isControl,
          controlType: detail.control_type,
        })
        if (!shouldNotify) {
          return
        }

        // 5. Stummschaltung und Blockierung prüfen
        if (store.isMuted(mid)) return
        if (meta.userId && store.isBlocked(meta.userId)) {
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

          // Background vs. Foreground Push: Bei aktiver WebSocket-Verbindung im Vordergrund
          // dürfen keine doppelten OS-Pushes getriggert werden!
          if (!isForeground) {
            void sendeGeraeteBenachrichtigung({
              titel: title,
              text: text,
            })
          }
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
