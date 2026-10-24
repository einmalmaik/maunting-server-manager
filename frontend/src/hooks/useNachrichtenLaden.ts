/**
 * Nachrichten laden und darstellen — der Anzeigeteil des Lesepfads.
 *
 * Der Block stand bis 10/2026 als 380-Zeilen-Funktion mitten in
 * `Messenger.tsx`. Herausgelöst ist er **unverändert**: dieselben Abbruchwächter,
 * dieselbe Reihenfolge, dieselben Häkchen. Was er braucht, kommt als ein
 * Optionsobjekt herein; die Namen darin sind die der Seite, damit der Umzug
 * nichts umbenennt und niemand beim Vergleich zweier Fassungen stolpert.
 *
 * Die Trennung ist die zwischen Entschlüsseln und Anzeigen:
 * `useKonversation` sagt, was ein Umschlag bedeutet, `werteUmschlaegeAus`
 * prüft, wer ihn geschrieben hat, und hier entsteht daraus der Verlauf —
 * Löschungen, Bearbeitungen, Reaktionen, Häkchen, Anheftung und Quittungen.
 *
 * **Warum es ein Hook ist und keine Hilfsfunktion.** Der Körper braucht
 * React-Zustand (`setMessages`), Refs gegen überholte Durchläufe und den
 * Konversations-Hook. Als freie Funktion müsste er all das einzeln
 * entgegennehmen, ohne dass die Seite dadurch kürzer würde.
 */

import type { Dispatch, SetStateAction } from 'react'

import type { ChatGroupItem } from '@/api/social'
import { sendReadReceipt } from '@/api/social'
import type { ChatMessage } from '@/components/social/ChatMessageBubble'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import type { Konversation } from '@/hooks/useKonversation'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import { vergissEigeneWirkungen } from '@/services/eigeneWirkungen'
import { anheftung } from '@/services/nachrichtAnheftung'
import {
  merkePartnerKennungen,
  merkePartnerQuittung,
  merkeQuittung,
  merkeQuittungKennungen,
  quittungsKennungen,
} from '@/services/quittungsstand'
import { anzeigeVerlauf, uebernimmLesungen } from '@/services/gespraech/uebernahme'

export interface NachrichtenLadenOptionen {
  /** Die Mailbox des offenen Gesprächs. Leer heißt: noch nichts zu laden. */
  blindMailboxId: string
  currentUserId: number
  activeContact: ChatContact | null
  activeGroup: ChatGroupItem | null
  /** Was ein Konto in der offenen Gruppe darf; aus `wirksameGruppenrechte`. */
  gruppenrechteVon: (konto: number) => Set<string>
  t: (schluessel: string, werte?: Record<string, unknown>) => string
  messages: ChatMessage[]
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>
  setLoadingMessages: Dispatch<SetStateAction<boolean>>
  setAngeheftet: Dispatch<SetStateAction<ChatMessage | null>>
  setVerfallSekunden: (sekunden: number) => void
  zeigeSystemzeile: (text: string, kennung?: string) => void
  markAsRead: (mailbox: string) => void
  isBlocked: (userId: number) => boolean
  readReceiptsEnabled: boolean
  konversation: Konversation
  identityRef: { current: E2eeIdentity | null }
  /** Woran ein laufender Abruf merkt, dass er zu spät kommt. */
  activeMailboxIdRef: { current: string }
  currentLoadSeqRef: { current: number }
  maxPartnerReadIdRef: { current: number }
  maxPartnerDeliveredIdRef: { current: number }
  highestIncomingIdDeliveredRef: { current: number }
  highestIncomingIdAcknowledgedRef: { current: number }
  /** Wirft; der Aufrufer entscheidet, ob er den Fehler zeigt. */
  sendE2eeControlMessage: (payload: Record<string, unknown>) => Promise<void>
}

/**
 * Gibt die Ladefunktion der Seite zurück.
 *
 * `isInitial` steuert nur die Ladeanzeige: ein Hintergrunddurchlauf soll den
 * Verlauf nicht grau werden lassen.
 */
export function useNachrichtenLaden(optionen: NachrichtenLadenOptionen) {
  const {
    blindMailboxId,
    currentUserId,
    activeContact,
    activeGroup,
    gruppenrechteVon,
    t,
    messages,
    setMessages,
    setLoadingMessages,
    setAngeheftet,
    setVerfallSekunden,
    zeigeSystemzeile,
    markAsRead,
    isBlocked,
    readReceiptsEnabled,
    konversation,
    identityRef,
    activeMailboxIdRef,
    currentLoadSeqRef,
    maxPartnerReadIdRef,
    maxPartnerDeliveredIdRef,
    highestIncomingIdDeliveredRef,
    highestIncomingIdAcknowledgedRef,
    sendE2eeControlMessage,
  } = optionen

  /** Der Name bleibt: die Aufrufer in der Seite heißen seit je so. */
  const loadMessages = async (isInitial = false) => {
    const currentMid = blindMailboxId
    if (!currentMid || !currentUserId) return
    if (activeMailboxIdRef.current !== currentMid) return
    // Erst entschlüsseln, wenn feststeht, welche Schlüssel dieses Gerät hat.
    // Ein Durchlauf während `loading` hätte keine, würde auf den Altpfad fallen
    // und dessen Ergebnis im Zwischenspeicher festschreiben — der richtige
    // Klartext käme danach nicht mehr durch.
    //
    // Das gilt seit der Umstellung auch für Gruppen: ihr Schlüssel kommt in
    // einem Hybridumschlag, der gegen den Geräteschlüssel versiegelt ist.
    const aktuelleIdentitaet = identityRef.current
    if (!aktuelleIdentitaet || aktuelleIdentitaet.state === 'loading') return
    const seq = ++currentLoadSeqRef.current

    if (isInitial && messages.length === 0) {
      setLoadingMessages(true)
    }
    try {
      // Der ganze Entschlüsselungsteil steht in `useKonversation`: welche
      // Mailbox, welches Verfahren, was ein Umschlag bedeutet. Hier bleibt die
      // Anzeige — Quittungen, Häkchen, Bearbeiten und Löschen.
      const gelesen = await konversation.liesUmschlaege()
      if (gelesen === null) return
      if (activeMailboxIdRef.current !== currentMid || currentLoadSeqRef.current !== seq) return

      // Der Schreibweg steht in `services/gespraech/uebernahme.ts`:
      // auswerten, Wirkungen anwenden, mischen, speichern. Hier bleibt die
      // Anzeige — Häkchen, Quittungen, Leiste, Ungelesen-Zähler.
      const uebernahme = await uebernimmLesungen(
        {
          mailbox: currentMid,
          eigeneId: currentUserId,
          contact: activeContact
            ? { userId: activeContact.userId, username: activeContact.username, isFriend: activeContact.isFriend }
            : null,
          group: activeGroup,
          gruppenrechteVon,
          t,
          maxPartnerReadId: maxPartnerReadIdRef.current,
          maxPartnerDeliveredId: maxPartnerDeliveredIdRef.current,
          quittiertGelesen: (bis) => {
            maxPartnerReadIdRef.current = Math.max(maxPartnerReadIdRef.current, bis)
            // Was der Partner gelesen hat, überlebt das Schließen des Chats.
            merkePartnerQuittung(currentMid, 'gelesen', bis)
          },
          quittiertZugestellt: (bis) => {
            maxPartnerDeliveredIdRef.current = Math.max(maxPartnerDeliveredIdRef.current, bis)
            merkePartnerQuittung(currentMid, 'zugestellt', bis)
          },
          quittiertGelesenIds: (ids) => merkePartnerKennungen(currentMid, 'gelesen', ids),
          quittiertZugestelltIds: (ids) => merkePartnerKennungen(currentMid, 'zugestellt', ids),
          markAsRead,
          setVerfallSekunden,
          zeigeSystemzeile,
        },
        gelesen,
        {
          istAktuell: () =>
            activeMailboxIdRef.current === currentMid && currentLoadSeqRef.current === seq,
        },
      )
      if (!uebernahme) return
      if (activeMailboxIdRef.current !== currentMid || currentLoadSeqRef.current !== seq) return
      const { processedList, lokalerVerlauf, maxIncomingId, unlesbar } = uebernahme

      setMessages((prev) =>
        anzeigeVerlauf(uebernahme, prev, maxPartnerReadIdRef.current, maxPartnerDeliveredIdRef.current),
      )

      /*
       * Erst jetzt den Lesestand übernehmen.
       *
       * Jeder vorzeitige `return` oben (fremder Chat, neuerer Durchlauf,
       * Schreibfehler) hat diese Lesung verworfen — dann bleibt der Stand
       * stehen und der nächste Abruf holt dieselben Umschläge noch einmal. Der
       * Klartext liegt dann schon in der Ablage, ein Ratchet-Schlüssel wird
       * also nicht ein zweites Mal verbraucht.
       *
       * Bis 22.10.2026 rückte der Stand schon im Leselauf vor. Startete
       * währenddessen ein zweiter Durchlauf (Takt, Sync-Ereignis), verwarf
       * dieser hier das Ergebnis des ersten — und der zweite fand nichts mehr:
       * Nachrichten der Gegenseite fehlten dauerhaft, bis die Seite neu lud.
       */
      konversation.uebernimmLesestand(gelesen)

      /**
       * Angewendet heißt erledigt — aber erst **hier**.
       *
       * Die aufbewahrten Wirkungen der eigenen Zweitgeräte fielen bis 10/2026
       * direkt nach dem Auswerten weg, also bevor der Verlauf geschrieben war.
       * Scheiterte der Schreibweg danach (Ablage voll, Messenger gesperrt),
       * stand die Bearbeitung nirgends mehr: die Quelle war verbraucht, die
       * Zeile unverändert. Jetzt fällt sie erst, wenn der neue Stand gesetzt
       * ist — ein zweiter Durchlauf wendet dieselbe Wirkung idempotent noch
       * einmal an.
       */
      await vergissEigeneWirkungen(currentMid).catch(() => {})

      /**
       * Die Leiste wird aus der Ablage abgeglichen, nicht aus dem Umschlag.
       *
       * Die Kennung überlebt das Hundert-Umschläge-Fenster, der Anheft-Umschlag
       * nicht. Deshalb ist die Ablage die Wahrheit und die Leiste nur ihre
       * Anzeige — das deckt beides ab: eine frisch übernommene Anheftung und
       * eine, die schon vor dem Öffnen des Chats galt. Nach dem `setMessages`,
       * denn im Updater wäre es ein Seiteneffekt mitten in der Berechnung.
       */
      const gemerkt = anheftung(currentMid).clientUuid
      setAngeheftet((bisher) => {
        if ((bisher?.clientUuid || '') === gemerkt) return bisher
        if (!gemerkt) return null
        // Noch nicht im Verlauf: keine Leiste. Der nächste Durchgang holt sie
        // nach, sobald die Zeile da ist — eine falsche wäre schlimmer als keine.
        return [...processedList, ...lokalerVerlauf].find((m) => m.clientUuid === gemerkt) || null
      })

      // Ungelesenes, das dieser Stand nicht mehr öffnen kann: Nur wenn wirklich
      // keine Nachrichten im lokalen Verlauf sind, zeigen wir einen Hinweis.
      // Wenn der Verlauf bereits intakt geladen ist, darf niemals eine falsche Warnung erscheinen.
      if (unlesbar > 0 && lokalerVerlauf.length === 0 && processedList.length === 0) {
        zeigeSystemzeile(
          t('messenger.unreadableOlderMessages'),
          `unlesbar-${currentMid}`,
        )
      }

      // Ungelesen-Zähler zurücksetzen
      markAsRead(currentMid)

      // Prüfen, ob der Ziel-Kontakt blockiert ist: Wenn blockiert, werden keinerlei
      // Zustell- oder Lesequittungen (delivery_receipt, read_receipt) an die Mailbox gesendet!
      // Dadurch verbleibt die Nachricht beim blockierten Absender dauerhaft auf genau 1 grauem Häkchen (✓).
      const isTargetBlocked = activeContact ? isBlocked(activeContact.userId) : false
      // In einer Gruppe keine Quittungen. Jedes Mitglied schickte je Nachricht
      // zwei in die Gruppenmailbox; bei
      // zehn Mitgliedern 18 Umschläge je Nachricht, und das Hundert-Fenster
      // hielt kaum noch echte Nachrichten (bis 26.09.2026).
      const istGruppe = Boolean(activeGroup)
      const isDocVisible = typeof document === 'undefined' || document.visibilityState === 'visible'

      /*
       * Die Nachrichten selbst, nicht nur eine Obergrenze.
       *
       * Eine Zahl über Umschlagkennungen zählte auch Kopien für andere Geräte
       * und Steuerpakete mit: der Absender konnte „gelesen“ sehen, ohne dass
       * diese Nachricht je auf einem Schirm war. Die Quittung nennt deshalb
       * die logischen Kennungen der Zeilen, die dieser Durchlauf abgelegt hat
       * — höchstens hundert, das deckt ein Mailboxfenster.
       */
      const eingehendeKennungen = Array.from(
        new Set(
          processedList
            .filter((m) => !m.isSelf && Boolean(m.clientUuid))
            .map((m) => m.clientUuid as string),
        ),
      )
      // Die zuletzt quittierten gehen mit: eine verlorene Quittung holt damit
      // die nächste nach, ohne dass eine hohe Zahl fremde Zeilen mitnimmt.
      const frueherZugestellt = quittungsKennungen(currentMid, 'zugestellt')
      const frueherGelesen = quittungsKennungen(currentMid, 'gelesen')
      const zugestelltIds = Array.from(
        new Set([...frueherZugestellt, ...eingehendeKennungen]),
      ).slice(-100)
      const gelesenIds = Array.from(
        new Set([...frueherGelesen, ...eingehendeKennungen]),
      ).slice(-100)

      // Sende Zustellbestätigung (delivery_receipt), sobald neue Nachrichten empfangen wurden
      const needsDelivery =
        !istGruppe &&
        maxIncomingId > 0 &&
        maxIncomingId > highestIncomingIdDeliveredRef.current &&
        !isTargetBlocked

      if (needsDelivery) {
        const idToDeliver = maxIncomingId
        /*
         * Der Stand steigt **vor** dem Versand, nicht danach.
         *
         * Bis 09/2026 wanderte er erst im `.then()` hoch. Jeder gescheiterte
         * Versand ließ ihn stehen, und der nächste Abruf ein paar Sekunden
         * später schickte dieselbe Quittung noch einmal. Bremste der Server
         * mit 429, hielt sich das von selbst am Leben: gemessen waren 64 % der
         * Umschläge in der Mailbox Zustellquittungen, und das Fenster fasst
         * hundert — verdrängt wurden Bearbeitungen und Reaktionen.
         *
         * Ein Verlust kostet nichts: `delivered_up_to_id` nennt eine
         * Obergrenze. Die nächste Nachricht bringt eine höhere Kennung und
         * damit eine Quittung, die den Bereich mit abdeckt.
         */
        highestIncomingIdDeliveredRef.current = Math.max(
          highestIncomingIdDeliveredRef.current,
          idToDeliver,
        )
        merkeQuittung(currentMid, 'zugestellt', idToDeliver)
        merkeQuittungKennungen(currentMid, 'zugestellt', zugestelltIds)
        sendE2eeControlMessage({
          type: 'delivery_receipt',
          delivered_up_to_id: idToDeliver,
          // Der genaue Weg; `delivered_up_to_id` bleibt für ältere Clients.
          delivered_ids: zugestelltIds,
          receiver_id: currentUserId,
          timestamp: new Date().toISOString(),
        }).catch(() => {})
      }

      // Send read receipt if there are new incoming unacknowledged messages and document is visible
      const needsRead =
        !istGruppe &&
        maxIncomingId > 0 &&
        maxIncomingId > highestIncomingIdAcknowledgedRef.current &&
        readReceiptsEnabled &&
        isDocVisible &&
        !isTargetBlocked

      if (needsRead) {
        const idToAck = maxIncomingId
        // Derselbe Grund wie bei der Zustellquittung: kumulativ, also lieber
        // einmal zu wenig als in jeder Runde erneut.
        highestIncomingIdAcknowledgedRef.current = Math.max(
          highestIncomingIdAcknowledgedRef.current,
          idToAck,
        )
        merkeQuittung(currentMid, 'gelesen', idToAck)
        merkeQuittungKennungen(currentMid, 'gelesen', gelesenIds)
        // Gelesen schließt zugestellt ein; die Zustellliste zieht mit.
        merkeQuittungKennungen(currentMid, 'zugestellt', gelesenIds)

        // Lesequittung über den neuen FIFO-Queue-Dienst an den Absender melden
        if (gelesenIds.length > 0) {
          const chat_type = istGruppe ? 'group' : 'direct'
          const chat_id = istGruppe ? String(activeGroup?.id) : String(activeContact?.userId)
          if (chat_id) {
            void sendReadReceipt({
              chat_type,
              chat_id,
              client_uuids: gelesenIds,
            }).catch(() => {})
          }
        }

        const dispatchReadReceipt = () => {
          sendE2eeControlMessage({
            type: 'read_receipt',
            read_up_to_id: idToAck,
            read_ids: gelesenIds,
            reader_id: currentUserId,
            timestamp: new Date().toISOString(),
          }).catch(() => {})
        }

        if (needsDelivery) {
          setTimeout(dispatchReadReceipt, 350)
        } else {
          dispatchReadReceipt()
        }
      }
    } catch {
      // Offline fallback
    } finally {
      if (isInitial && activeMailboxIdRef.current === currentMid) {
        setLoadingMessages(false)
      }
    }
  }


  return loadMessages
}
