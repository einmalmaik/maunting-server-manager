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
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { sendeGeraeteBenachrichtigung, pruefeUndFrageGeraeteBerechtigung } from '@/lib/benachrichtigung'
import { abonniere, kuendige } from '@/services/pushAbo'
import { useMessengerNotificationStore, playNotificationChime } from '@/stores/messengerNotificationStore'
import { NotificationService } from '@/services/notificationService'
import { loadCalendarEventsOfflineFirst } from '@/lib/offlineSync'
import { sendE2eeDeliveryReceipt, checkAndDispatchPendingDeliveryReceipts } from '@/services/deliveryReceiptService'

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

/**
 * Fällige Vorkommen aus Serien, die der Server nicht lesen kann.
 *
 * Er liefert in `/calendar/due-reminders` nur, was er selbst ausbreiten kann.
 * Bei einem Ende-zu-Ende verschlüsselten Termin steht die Wiederholungsregel
 * als `sv-cal-v1:`-Umschlag in seiner Datenbank — und das bleibt so. Also
 * rechnet dieses Gerät sie aus seinem eigenen Spiegel aus.
 *
 * Dieselben Fenster wie serverseitig (`CalendarService.get_due_reminders`) und
 * derselbe Schlüsselbau, damit nichts doppelt meldet, wenn der Server einen
 * Termin doch lesen kann.
 */
async function faelligeSerienVorkommen(
  userId: number | undefined,
  zeitzone: string | null | undefined,
): Promise<CalendarDueReminder[]> {
  const jetzt = new Date()
  const bis = new Date(jetzt.getTime() + 50 * 3600_000)
  const { events } = await loadCalendarEventsOfflineFirst(
    jetzt.toISOString(),
    bis.toISOString(),
    undefined,
    userId,
    zeitzone ?? null,
  )

  const faellig: CalendarDueReminder[] = []
  for (const ev of events) {
    // Nur Serien: Einzeltermine meldet der Server bereits, und die trügen
    // sonst zwei Meldungen.
    if (!ev.istSerie) continue

    const start = new Date(ev.start)
    const stunden = (start.getTime() - jetzt.getTime()) / 3600_000
    let hinweis: string | null = null
    let stufe: string | null = null
    if (stunden > 25 && stunden <= 49) {
      hinweis = 'in 2 Tagen'
      stufe = '48h'
    } else if (stunden >= 0 && stunden <= 25) {
      hinweis = stunden > 2 ? 'in 1 Tag' : 'in Kürze'
      stufe = '24h'
    }
    if (!hinweis || !stufe) continue

    faellig.push({
      event_id: ev.event_id,
      title: ev.title,
      start: start.toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }),
      location: ev.location || '',
      time_hint: hinweis,
      key: `${userId ?? 0}_${ev.event_id}_${ev.vorkommen}_${stufe}`,
    })
  }
  return faellig
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
  const { t } = useTranslation()
  const { user, isAuthenticated } = useAuthStore()
  const seenIncidentsRef = useRef<Set<string>>(loadSeenSet(SESSION_INCIDENTS_KEY))
  const seenRemindersRef = useRef<Set<string>>(loadSeenSet(SESSION_REMINDERS_KEY))
  const isPollingRef = useRef(false)

  useEffect(() => {
    if (!isAuthenticated || !user) {
      return
    }

    // Bei aktivem Dienst Berechtigungen prüfen & abfragen
    if (user.device_notifications !== false) {
      // Erst fragen, dann abonnieren: `abonniere` fragt bewusst nicht selbst
      // nach der Erlaubnis und tut ohne sie nichts. Andersherum wäre das Abo
      // beim ersten Start immer daneben.
      //
      // Das Abonnement ist der Weg für die geschlossene Anwendung. Die Meldungen
      // weiter unten in dieser Datei sind der Weg für den offenen Tab; beide
      // nebeneinander doppeln nichts, weil `sw.js` einen Push verwirft, solange
      // ein Fenster im Vordergrund ist.
      void pruefeUndFrageGeraeteBerechtigung().then((erlaubt) => {
        if (erlaubt) void abonniere()
      })
    } else {
      // Der Schalter steht auf aus. Dann gehört auch die Zustelladresse weg und
      // nicht nur die Anzeige unterdrückt — sonst hinge am Konto weiter ein
      // Abo, das der Server bei jeder Nachricht bedient.
      void kuendige()
    }

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
                titel: t('notifications.incidentTitle', { server: inc.server_name }),
                text: t('notifications.incidentText', { titel: inc.title, art: inc.type }),
              })

              // Pop-up Toast im Interface
              toast.error(t('notifications.incidentToast', { server: inc.server_name, titel: inc.title }))
            }
          }
          if (updatedIncidents) {
            saveSeenSet(SESSION_INCIDENTS_KEY, seenIncidentsRef.current)
          }
        }

        // 2. Fällige Termine (24h / 48h) abrufen
        const vomServer = await api<CalendarDueReminder[]>('/calendar/due-reminders').catch(() => [])
        // Serien, deren Regel Ende-zu-Ende verschlüsselt ist, fehlen in der
        // Serverliste — er kann sie nicht ausbreiten. Dieses Gerät hat den
        // Schlüssel und trägt sie nach; ohne das meldete sich ausgerechnet der
        // Geburtstag nie, um den es bei Serienterminen zuallererst geht.
        const vomGeraet = await faelligeSerienVorkommen(user?.id, user?.time_zone).catch(() => [])
        const reminders = [...(Array.isArray(vomServer) ? vomServer : []), ...vomGeraet]
        if (Array.isArray(reminders)) {
          let updatedReminders = false
          for (const rem of reminders) {
            if (rem.key && !seenRemindersRef.current.has(rem.key)) {
              seenRemindersRef.current.add(rem.key)
              updatedReminders = true

              // Push-Benachrichtigung (OS Windows / Android)
              void sendeGeraeteBenachrichtigung({
                titel: t('notifications.reminderTitle', { wann: rem.time_hint }),
                text: t('notifications.reminderText', { titel: rem.title, start: rem.start }),
              })

              // Pop-up Toast im Interface
              toast.success(t('notifications.reminderToast', { wann: rem.time_hint, titel: rem.title }))
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
      void useMessengerNotificationStore.getState().syncMailboxDirectoryFromBackend(user.id).then(() => {
        void checkAndDispatchPendingDeliveryReceipts(user.id)
      })
    }

    // Sofortiger initialer Check nach Login
    if (user.device_notifications !== false) {
      void checkAlerts()
    }

    const interval = setInterval(() => {
      if (user.device_notifications !== false) {
        void checkAlerts()
      }
    }, POLL_INTERVAL_MS)

    // Sofortige Echtzeit-Push-Benachrichtigung für Freundschaftsanfragen & Messenger-Nachrichten
    const handleSyncEvent = (e: Event) => {
      const ce = e as CustomEvent<any>
      const detail = ce.detail
      if (detail?.type === 'friend_request_received') {
        const senderName = detail.from_username || t('notifications.someUser')
        if (user.device_notifications !== false) {
          void sendeGeraeteBenachrichtigung({
            titel: t('notifications.friendRequestTitle'),
            text: t('notifications.friendRequestText', { name: senderName }),
          })
          toast.success(t('notifications.friendRequestToast', { name: senderName }))
        }
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
          if (typeof window !== 'undefined') {
            window.dispatchEvent(
              new CustomEvent('msm:messages-updated', {
                detail: { blind_mailbox_id: mid, is_control: true, control_type: detail.control_type },
              })
            )
          }
          return
        }

        // Automatische Zustellbestätigung (2 graue Häkchen beim Absender):
        // Sobald das Gerät des Empfängers die Nachricht via SSE erhalten hat (Empfänger hat Internet/Online-Status),
        // wird unmittelbar eine Zustellquittung (delivery_receipt) an die Mailbox übermittelt — auch wenn der
        // Chat noch nicht geöffnet wurde!
        const incomingSenderId = Number(senderId)
        const recipientUserId = Number(user?.id)
        const isGroup = Boolean(detail.is_group || store.mailboxDirectory[mid]?.isGroup)
        if (
          !isGroup &&
          incomingSenderId &&
          recipientUserId &&
          incomingSenderId !== recipientUserId &&
          detail.id &&
          !store.isBlocked(incomingSenderId)
        ) {
          void sendE2eeDeliveryReceipt({
            blindMailboxId: mid,
            envelopeId: Number(detail.id),
            senderUserId: incomingSenderId,
            currentUserId: recipientUserId,
          })
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

        /**
         * „Hier liegt etwas Neues" — und zwar **vor** der Stummschaltung.
         *
         * Die Erwähnungswache hing bis 20.09.2026 am Ungelesen-Zähler, und den
         * überspringt der stumme Pfad gleich darunter. In einer stummen Gruppe
         * erschien deshalb nicht einmal das @-Abzeichen, obwohl genau das der
         * Sinn der Sache ist: kein Ton, aber sehen, dass man gemeint war.
         *
         * Das Ereignis nennt nur die Mailbox. Es trägt keinen Inhalt und löst
         * keine Meldung aus; wer daran hängt, entscheidet selbst, ob er
         * hinsieht.
         */
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('msm:mailbox-neu', { detail: { mid } }))
        }

        // 5. Stummschaltung und Blockierung prüfen
        if (store.isMuted(mid)) return
        if (meta.userId && store.isBlocked(meta.userId)) {
          return
        }

        const senderOrChat = meta.name
        const title = t('notifications.messageTitle', { name: senderOrChat })
        const text = meta.isGroup
          ? t('notifications.messageInGroup', { name: meta.name })
          : t('notifications.messageFrom', { name: senderOrChat })

        // Ungelesen-Zähler im Store erhöhen
        store.incrementUnread(mid)

        // Nur akustisch signalisieren und benachrichtigen, wenn Gerätebenachrichtigung aktiv ist
        if (user?.device_notifications !== false) {
          playNotificationChime()
          toast.success(
            meta.isGroup
              ? t('notifications.messageToastGroup', { name: meta.name })
              : t('notifications.messageToastDirect', { name: senderOrChat }),
          )

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
