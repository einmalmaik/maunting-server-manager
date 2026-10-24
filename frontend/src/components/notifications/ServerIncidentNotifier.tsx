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
import { kuendigeMailboxPush } from '@/services/mailboxPush'
import { useMessengerNotificationStore, chimeEntsperren } from '@/stores/messengerNotificationStore'
import { meldeChat } from '@/services/benachrichtigung/chatMeldung'
import { loadCalendarEventsOfflineFirst } from '@/lib/offlineSync'
import { erinnerungenAus, PLAN_TAGE, type GeplanteErinnerung } from '@/lib/erinnerungsplan'
import { CALENDAR_CIPHERTEXT_PREFIX } from '@/services/notesCalendarCrypto'

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
  start_iso?: string
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

/**
 * In der Android-App meldet nicht diese Schleife Termine, sondern Android
 * selbst zur geplanten Zeit, auch bei geschlossener App (`lib/erinnerungsplan.ts`).
 * Beides zugleich meldete jeden Termin zweimal.
 */
function aufAndroid(): boolean {
  return (
    typeof window !== 'undefined' &&
    ('__TAURI_INTERNALS__' in window || '__TAURI__' in window) &&
    /android/i.test(navigator.userAgent)
  )
}

async function erinnerungenUebergeben(liste: GeplanteErinnerung[]): Promise<void> {
  // Nachgeladen: im Panel und am Desktop gehört die Brücke nicht ins Startbündel.
  const { erinnerungenPlanen } = await import('@/desktop/tauri')
  await erinnerungenPlanen(liste)
}

const POLL_INTERVAL_MS = 20_000
const BASE_INCIDENTS_KEY = 'msm_alerted_incidents'
const BASE_REMINDERS_KEY = 'msm_alerted_reminders'

function getAccountStorageKey(baseKey: string, userId?: number): string {
  return userId ? `${baseKey}_user_${userId}` : baseKey
}

function loadSeenSet(storageKey: string): Set<string> {
  try {
    const raw = localStorage.getItem(storageKey) || sessionStorage.getItem(storageKey)
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr)) return new Set(arr)
    }
  } catch {}
  return new Set<string>()
}

function saveSeenSet(storageKey: string, set: Set<string>) {
  try {
    const arr = Array.from(set).slice(-200) // Maximal 200 Einträge
    const val = JSON.stringify(arr)
    localStorage.setItem(storageKey, val)
    sessionStorage.setItem(storageKey, val)
  } catch {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(Array.from(set).slice(-100)))
    } catch {}
  }
}

export function ServerIncidentNotifier() {
  const { t, i18n } = useTranslation()
  const { user, isAuthenticated } = useAuthStore()
  const incidentsKey = getAccountStorageKey(BASE_INCIDENTS_KEY, user?.id)
  const remindersKey = getAccountStorageKey(BASE_REMINDERS_KEY, user?.id)

  const seenIncidentsRef = useRef<Set<string>>(loadSeenSet(incidentsKey))
  const seenRemindersRef = useRef<Set<string>>(loadSeenSet(remindersKey))
  const isPollingRef = useRef(false)
  // Was zuletzt an Android ging; ohne Änderung wird nicht neu geplant.
  const planRef = useRef<string | null>(null)
  const currentUserIdRef = useRef<number | undefined>(user?.id)

  // Ein Browser hebt die Autoplay-Sperre nur in einer echten Geste auf.
  // Pontis, Web und MSS mounten diesen Baustein; damit gilt es überall.
  useEffect(() => {
    chimeEntsperren()
  }, [])

  useEffect(() => {
    if (currentUserIdRef.current !== user?.id) {
      currentUserIdRef.current = user?.id
      seenIncidentsRef.current = loadSeenSet(incidentsKey)
      seenRemindersRef.current = loadSeenSet(remindersKey)
    }
  }, [user?.id, incidentsKey, remindersKey])

  useEffect(() => {
    if (!isAuthenticated || !user) {
      // Abgemeldet: die Erinnerungen des Kontos fallen vom Gerät (AGENTS.md Punkt 50).
      if (aufAndroid() && planRef.current !== '[]') {
        planRef.current = '[]'
        void erinnerungenUebergeben([]).catch(() => {})
      }
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
      //
      // Erst die Mailboxen, dann das Konto: `kuendige` beendet am Ende das
      // Abonnement im Browser, und danach gibt es keine Adresse mehr
      // auszutragen. Dass der Schalter auch für den Mailbox-Weg gilt, kann nur
      // hier entschieden werden — dort steht kein Konto mehr, an dem der
      // Server ihn nachschlagen könnte.
      void kuendigeMailboxPush().then(() => kuendige())
      if (aufAndroid() && planRef.current !== '[]') {
        planRef.current = '[]'
        void erinnerungenUebergeben([]).catch(() => {})
      }
    }

    const planen = async () => {
      const jetzt = Date.now()
      const { events } = await loadCalendarEventsOfflineFirst(
        new Date(jetzt).toISOString(),
        new Date(jetzt + PLAN_TAGE * 86_400_000).toISOString(),
        undefined,
        user.id,
        user.time_zone ?? null,
      )
      const format = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'short', timeStyle: 'short' })
      const plan = erinnerungenAus(
        events,
        jetzt,
        user.id,
        t,
        (datum) => format.format(datum),
        (titel) => titel.startsWith(CALENDAR_CIPHERTEXT_PREFIX),
      )
      // Was sofort fällig ist, trägt bei jedem Lauf eine neue Zeit; es zählt
      // nur, ob es dabei ist.
      const abdruck = JSON.stringify(plan.map((e) => [e.schluessel, e.zeit > jetzt ? e.zeit : 0, e.titel, e.text]))
      if (abdruck === planRef.current) return
      await erinnerungenUebergeben(plan)
      planRef.current = abdruck
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
            saveSeenSet(incidentsKey, seenIncidentsRef.current)
          }
        }

        // 2. Termine: in der Android-App plant Android sie selbst.
        if (aufAndroid()) {
          await planen().catch(() => {})
          return
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

              const startText = rem.start_iso
                ? new Date(rem.start_iso).toLocaleString(i18n.language || 'de-DE', { dateStyle: 'short', timeStyle: 'short' })
                : rem.start

              // Push-Benachrichtigung (OS Windows / Android)
              void sendeGeraeteBenachrichtigung({
                titel: t('notifications.reminderTitle', { wann: rem.time_hint }),
                text: t('notifications.reminderText', { titel: rem.title, start: startText }),
              })

              // Pop-up Toast im Interface
              toast.success(t('notifications.reminderToast', { wann: rem.time_hint, titel: rem.title }))
            }
          }
          if (updatedReminders) {
            saveSeenSet(remindersKey, seenRemindersRef.current)
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
      // Nur die Liste der Mailboxen auffrischen. **Keine Quittung hier**: ein
      // Wecksignal sagt nicht, ob der Umschlag für dieses Gerät war, ob er
      // sich öffnen ließ oder ob er überhaupt eine Nachricht ist. Eine
      // Zustellquittung entsteht deshalb erst nach dem lokalen Commit im
      // Lesepfad (`useNachrichtenLaden`), nie aus einer Umschlagliste.
      void useMessengerNotificationStore.getState().syncMailboxDirectoryFromBackend(user.id)
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
      } else if (detail?.type === 'achievement_unlocked') {
        // Bis 09/2026 verschickte das Backend dieses Ereignis, und niemand
        // hörte zu: neue Abzeichen sah nur, wer die Liste selbst öffnete.
        const ach = detail.achievement
        if (ach?.title) {
          toast.success(t('notifications.achievementUnlockedToast', { title: ach.title, points: ach.points ?? 0 }))
        }
      } else if (detail?.type === 'e2ee_blind_message') {
        if (!detail.blind_mailbox_id) return
        /*
         * Geteilte Mailbox und Gerätepostfach gehen durch dieselbe Meldestelle.
         * Die Regeln (eigene Aktion, Steuerung, Stumm, Blockiert, offener Chat)
         * stehen dort; hier bleibt nur die Übersetzung des Ereignisses.
         */
        meldeChat({
          mailbox: detail.blind_mailbox_id,
          senderId: detail.sender_user_id ?? detail.sender_id,
          empfaengerId: detail.recipient_id,
          clientUuid: detail.client_uuid ?? null,
          istGruppe: detail.is_group,
          meldenErlaubt: detail.melden !== false,
          isControl: Boolean(detail.is_control),
          controlType: detail.control_type ?? null,
          ereignis: detail.ereignis ?? null,
          t,
        })
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
