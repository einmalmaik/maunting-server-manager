/**
 * Eine Chat-Nachricht melden — für geteilte Mailbox **und** Gerätepostfach.
 *
 * Bis 10/2026 lag diese Entscheidung nur im `ServerIncidentNotifier`, und der
 * hörte ausschließlich auf `e2ee_blind_message` aus der geteilten Mailbox. Das
 * Gerätepostfach erzeugt kein solches Ereignis: eine dort zugestellte Nachricht
 * blieb ohne Zahl und ohne Ton, obwohl sie längst lokal lag. Wer den Messenger
 * schloss, sah gar nichts mehr.
 *
 * Deshalb entscheidet **eine** Stelle, was mit einer neuen Chat-Zeile geschieht;
 * der Ereignis-Listener und der Postfach-Hintergrundlauf rufen sie beide auf.
 * Die Regeln selbst stehen unverändert in `planeMeldung` (eigene Aktion,
 * Steuerung, Stumm, Blockiert, offener Chat) — hier stehen nur die Tatsachen,
 * die der Hub nicht kennen kann, und die Ausführung.
 */

import { useAuthStore } from '@/stores/authStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { NotificationService } from '@/services/notificationService'

import { beanspruche } from './einmalig'
import { melde, planeMeldung } from './hub'

type Uebersetzer = (schluessel: string, werte?: Record<string, unknown>) => string

export interface ChatMeldungEingang {
  /** Die blinde Mailbox des Gesprächs. */
  mailbox: string
  /** Wer die Nachricht geschickt hat, soweit bekannt. */
  senderId?: number | string | null
  /** Die logische Kennung; hält dieselbe Nachricht von einem zweiten Weg fern. */
  clientUuid?: string | null
  /** Aus dem Ereignis, wenn der Server es sagt. */
  istGruppe?: boolean
  /** Wen es trifft, soweit der Server das sagen kann (blinde Mailboxen: nie). */
  empfaengerId?: number | string | null
  /** Der Server hat **diesen** Client als Zusteller gewählt. */
  meldenErlaubt?: boolean
  /** Steuerumschlag (Quittung, Bearbeitung, …): keine Meldung. */
  isControl?: boolean
  controlType?: string | null
  /** Die Kennung des Servers für die Fenster-Dedupe. */
  ereignis?: string | null
  t: Uebersetzer
}

/**
 * Entscheiden, zählen, klingeln und einblenden.
 *
 * Der Aufrufer hat die Nachricht **lokal abgelegt**; ein Wecksignal allein
 * genügt nicht. Genau deshalb darf auch der Postfachlauf hier hereinkommen:
 * was er meldet, ist entschlüsselt und gespeichert.
 */
export function meldeChat(eingang: ChatMeldungEingang): void {
  const mid = eingang.mailbox
  if (!mid) return

  const user = useAuthStore.getState().user
  const store = useMessengerNotificationStore.getState()
  const senderId = eingang.senderId
  const controlType = eingang.controlType ?? null
  const istSteuerung = Boolean(
    eingang.isControl || (controlType && controlType !== 'message' && controlType !== 'normal'),
  )

  // 1. Ein eigenes anderes Gerät hat gelesen: den Zähler hier zurücksetzen.
  if (
    controlType === 'read_receipt' &&
    NotificationService.isOutgoingEcho(senderId, user?.id)
  ) {
    store.markAsRead(mid)
    return
  }

  // 2. Die eigene Aktion (auch über KI/Worker im eigenen Namen) meldet nichts.
  if (NotificationService.isOutgoingEcho(senderId, user?.id)) return

  // 3. Steuerumschläge sind keine Nachrichten; die Oberfläche erfährt nur,
  //    dass sie sich den Verlauf neu ansehen soll.
  if (istSteuerung) {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('msm:messages-updated', {
          detail: { blind_mailbox_id: mid, is_control: true, control_type: controlType },
        }),
      )
    }
    return
  }

  /*
   * Dieselbe Nachricht kann in einem kurzen Fenster über zwei Wege eintreffen
   * (geteilte Mailbox und Gerätepostfach, etwa während eines Umzugs). `melde`
   * hält Ton und Einblendung zurück; der Ungelesen-Zähler liegt davor und würde
   * sonst doppelt steigen. Der Schlüssel gilt über alle Fenster.
   */
  if (eingang.clientUuid && !beanspruche(`zaehlen:${mid}:${eingang.clientUuid}`, 5000)) {
    return
  }

  let meta = store.mailboxDirectory[mid]
  if (!meta) {
    if (user?.id) void store.syncMailboxDirectoryFromBackend(user.id)
    meta = {
      name: eingang.t('notifications.someUser'),
      isGroup: Boolean(eingang.istGruppe),
    }
  }

  const isDocVisible =
    typeof document !== 'undefined' && document.visibilityState === 'visible'
  const isWindowFocused =
    typeof document !== 'undefined' &&
    (typeof document.hasFocus !== 'function' || document.hasFocus())
  const isForeground = isDocVisible && isWindowFocused

  const lage = {
    art: 'chat' as const,
    schalterAn: user?.device_notifications !== false,
    absenderId: senderId,
    eigeneId: user?.id,
    empfaengerId: eingang.empfaengerId ?? user?.id,
    istSteuerung,
    stumm: store.isMuted(mid),
    blockiert: Boolean(meta.userId && store.isBlocked(meta.userId)),
    chatOffen: store.activeMailboxId === mid && isDocVisible && isWindowFocused,
    fensterImVordergrund: isForeground,
    // Der Postfachlauf kennt keinen serverseitigen Zusteller; die
    // Fenster-Dedupe unten hält die zweite Kopie trotzdem zurück.
    meldenErlaubt: eingang.meldenErlaubt !== false,
  }
  const plan = planeMeldung(lage)

  /**
   * „Hier liegt etwas Neues" vor der Stummschaltung: die Erwähnungswache darf
   * auch in einem stummen Chat sehen, dass jemand einen gemeint hat.
   */
  const erwaehnungSichtbar =
    plan.grund !== 'eigene-aktion' &&
    plan.grund !== 'steuerung' &&
    plan.grund !== 'nicht-empfaenger' &&
    plan.grund !== 'chat-offen'
  if (erwaehnungSichtbar && typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('msm:mailbox-neu', { detail: { mid } }))
  }

  if (plan.grund) return

  const senderOrChat = meta.name
  const title = eingang.t('notifications.messageTitle', { name: senderOrChat })
  const text = meta.isGroup
    ? eingang.t('notifications.messageInGroup', { name: meta.name })
    : eingang.t('notifications.messageFrom', { name: senderOrChat })

  if (plan.zaehlen) store.incrementUnread(mid)

  void melde(lage, {
    schluessel:
      eingang.ereignis ||
      `chat:${mid}:${eingang.clientUuid || ''}`,
    titel: title,
    text,
    toastText: meta.isGroup
      ? eingang.t('notifications.messageToastGroup', { name: meta.name })
      : eingang.t('notifications.messageToastDirect', { name: senderOrChat }),
  })
}
