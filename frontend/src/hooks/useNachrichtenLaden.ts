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
import type { ChatMessage } from '@/components/social/ChatMessageBubble'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import type { Konversation } from '@/hooks/useKonversation'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import { vergissEigeneWirkungen } from '@/services/eigeneWirkungen'
import { ereignisseAusNachrichten } from '@/services/funkenService'
import { sessionChatCache } from '@/services/klartextSpeicher'
import {
  entfernePlatzhalterUndDubletten,
  isOptimisticMessage,
  loadLocalMessages,
  mischeVerlauf,
  saveLocalMessages,
} from '@/services/messengerLocalStore'
import { tilgeInhalt, tilgeNachrichtLokal } from '@/services/nachrichtLoeschen'
import { sichereAnhaenge } from '@/services/medienSicherung'
import { wendeReaktionenAn } from '@/services/reaktionen'
import { anheftung } from '@/services/nachrichtAnheftung'
import { hakenFuer } from '@/services/auswertung/quittung'
import { merkePartnerQuittung, merkeQuittung } from '@/services/quittungsstand'
import { werteUmschlaegeAus } from '@/services/umschlagAuswertung'
import { useFunkenStore } from '@/stores/funkenStore'

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

      const {
        decryptedList,
        aenderungen,
        loeschungen,
        reaktionen,
        maxPartnerReadId,
        maxPartnerDeliveredId,
        maxIncomingId,
        unlesbar,
      } = await werteUmschlaegeAus(gelesen, {
        currentMid,
        currentUserId,
        activeContact,
        activeGroup,
        gruppenrechteVon,
        t,
        quittiertGelesen: (bis) => {
          maxPartnerReadIdRef.current = Math.max(maxPartnerReadIdRef.current, bis)
          // Was der Partner gelesen hat, überlebt das Schließen des Chats: die
          // Quittung selbst wird nicht wieder gelesen (Punkt 180).
          merkePartnerQuittung(currentMid, 'gelesen', bis)
        },
        quittiertZugestellt: (bis) => {
          maxPartnerDeliveredIdRef.current = Math.max(maxPartnerDeliveredIdRef.current, bis)
          merkePartnerQuittung(currentMid, 'zugestellt', bis)
        },
        markAsRead,
        setVerfallSekunden,
        zeigeSystemzeile,
      })
      const findeAenderung = (m: ChatMessage) =>
        aenderungen.finde(m, (urheber) => urheber !== undefined && Number(urheber) === Number(m.senderId))

      /**
       * Wessen Löschbefehl befolgt wird.
       *
       * Der eigene Absender immer. In einer Gruppe zusätzlich, wer
       * `delete_messages` trägt — und das ist seit 09/2026 auch wirklich dieses
       * Recht. Vorher wurde hier `can_pin_messages` geprüft, also das Recht,
       * eine Nachricht **anzuheften**: ein Moderator mit Löschrecht und ohne
       * Heftrecht wurde ignoriert, einer mit Heftrecht und ohne Löschrecht kam
       * durch. Die Rechtelage kommt jetzt aus derselben Stelle wie im
       * Rechte-Dialog, inklusive der Rollen aus dem verschlüsselten Block.
       *
       * Die ehrliche Grenze steht hier und nicht im Werbetext: Moderation unter
       * Ende-zu-Ende-Verschlüsselung ist eine Bitte, die Clients befolgen —
       * keine Tatsache, die der Server durchsetzt. Wer die Nachricht schon
       * gelesen hat, behält sie. Das gilt für Signal und WhatsApp genauso.
       */
      const findeLoeschung = (m: ChatMessage) =>
        loeschungen.finde(m, (urheber) => {
          if (urheber === undefined) return false
          if (Number(urheber) === Number(m.senderId)) return true
          if (!activeGroup) return false
          return gruppenrechteVon(Number(urheber)).has('delete_messages')
        })

      /**
       * Die Wirkungen dieses Durchlaufs auf eine Zeile, die es schon gibt.
       *
       * Gebraucht an **zwei** Stellen, und genau darin lag ein Fehler: der
       * lokale Verlauf wendete Löschung und Reaktionen an, die noch
       * unbestätigten Zeilen aus dem React-Zustand dagegen gar nichts.
       * `mischeVerlauf` lässt bei zwei optimistischen Fassungen die aus dem
       * Zustand gewinnen — die gerade berechnete Reaktion wurde damit wieder
       * überschrieben und so weggespeichert. Am laufenden System: eine
       * Nachricht, die keine Reaktion mehr annahm, für immer.
       *
       * Bearbeitungen fehlten hier ganz. Sie standen allein im Zweig für frisch
       * entschlüsselte Umschläge, und der kennt nur, was noch im
       * Hundert-Umschläge-Fenster liegt. Alles Ältere blieb beim Empfänger
       * unverändert stehen, auch nach dem Neuladen.
       */
      const wendeWirkungenAn = (m: ChatMessage): ChatMessage => {
        const loeschung = findeLoeschung(m)
        if (loeschung && !m.isDeleted) return tilgeInhalt(m, loeschung.deletedAt) as ChatMessage

        let zeile = m
        const aenderung = findeAenderung(zeile)
        if (aenderung && zeile.text !== aenderung.newText) {
          zeile = {
            ...zeile,
            originalText: zeile.text,
            text: aenderung.newText,
            isEdited: true,
            editedAt: aenderung.editedAt,
          }
        }

        const neue = wendeReaktionenAn(zeile.reaktionen, reaktionen.finde(zeile))
        return neue === zeile.reaktionen ? zeile : { ...zeile, reaktionen: neue }
      }

      // Apply Edits, Deletions, and Read Status
      // Gelöscht = gelöscht. Kein Originaltext wird aufbewahrt (Zero Knowledge).
      const processedList: ChatMessage[] = decryptedList.map((msg) => {
        let text = msg.text
        let isEdited = false
        let editedAt: string | undefined = undefined
        let isDeleted = false
        let deletedAt: string | undefined = undefined
        let originalText: string | undefined = undefined

        const aenderung = findeAenderung(msg)
        if (aenderung) {
          originalText = text
          text = aenderung.newText
          isEdited = true
          editedAt = aenderung.editedAt
        }

        const loeschung = findeLoeschung(msg)
        if (loeschung) {
          isDeleted = true
          deletedAt = loeschung.deletedAt
          // Kein originalText bei Löschung — gelöscht ist gelöscht.
          originalText = undefined
        }

        // Dynamisches Häkchen-System (WhatsApp-Style), eine Stelle für beide
        // Wege: gelesen (read_receipt) vor zugestellt (delivery_receipt), und
        // der gespeicherte Stand ist die Untergrenze — siehe `hakenFuer`.
        const { isRead, isDelivered, status } = hakenFuer(msg, {
          maxPartnerReadId,
          maxPartnerDeliveredId,
        })

        const fertig = {
          ...msg,
          text,
          isEdited,
          editedAt,
          isDeleted,
          deletedAt,
          originalText,
          isDelivered,
          isRead,
          status,
          // Reaktionen aus diesem Fenster in die Zeile schreiben. Der Stand aus
          // der Ablage kommt gleich beim Mischen dazu; `wendeReaktionenAn`
          // trägt hier nur die neu gesehenen Meldungen nach.
          reaktionen: wendeReaktionenAn(msg.reaktionen, reaktionen.finde(msg)),
        }
        // Ausblenden reicht nicht: was hier stehen bleibt, schreibt
        // `saveLocalMessages` gleich wieder auf die Platte — Text und Anhang
        // einer gelöschten Nachricht eingeschlossen.
        return isDeleted && deletedAt ? tilgeInhalt(fertig, deletedAt) : fertig
      })

      // Abort if the user has navigated to another chat in the meantime or a newer load completed
      if (activeMailboxIdRef.current !== currentMid || currentLoadSeqRef.current !== seq) return

      /**
       * Der eigene Gesprächsanteil steht nur hier: eine Ratchet-Nachricht kann
       * ihr Absender nicht öffnen. Ein Ersetzen statt Zusammenführen würde
       * alles selbst Geschriebene bei jedem Abruf wegwischen.
       *
       * **Seit 09/2026 auch für Gruppen.** Dort ließ sich die eigene Nachricht
       * zwar immer schon lesen (Sender Keys sind symmetrisch), weshalb der
       * lokale Verlauf verzichtbar schien. Verzichtbar war er aber nur für den
       * Text: Markierungen, die Suche über alle Chats und die Übersicht „an
       * mich" lesen alle aus dieser Ablage. Ohne sie endeten Gruppen in jeder
       * dieser Ansichten als leere Stelle.
       */
      /*
       * Einmal aufräumen, vor dem Lesen.
       *
       * Hier liegen die Altlasten aus der Zeit, in der ein unlesbarer Umschlag
       * als „Verschlüsselte Nachricht" gespeichert wurde: der Platzhalter neben
       * der echten Zeile, dieselbe Nachricht zweimal. Die Anzeige mischt sie
       * ohnehin zusammen; diese Bereinigung nimmt sie auch aus der Ablage, damit
       * die Verlaufsübergabe sie nicht weiterreicht. Konservativ: ein
       * alleinstehender Platzhalter bleibt und wird nur ausgeblendet.
       */
      const verlaufsArt = activeGroup ? ('gruppe' as const) : ('direkt' as const)
      await entfernePlatzhalterUndDubletten(currentMid, verlaufsArt).catch(() => ({}))
      if (activeMailboxIdRef.current !== currentMid || currentLoadSeqRef.current !== seq) return

      const rohesLokal = await loadLocalMessages(currentMid)
      if (activeMailboxIdRef.current !== currentMid || currentLoadSeqRef.current !== seq) return

      /**
       * Eine Löschung, die hier ankommt, gilt auch für das, was schon auf der
       * Platte liegt.
       *
       * Der Steuerumschlag nennt eine Nachricht, die dieses Gerät längst hat.
       * Ohne diesen Durchgang blieben zwei Fassungen zurück: die Zeile im
       * eigenen Verlauf mit Text und Anhang, und der abgelegte
       * Umschlag-Klartext. Beide würden beim nächsten Öffnen des Gesprächs
       * wieder gelesen — die Nachricht wäre „für alle gelöscht" und stünde
       * trotzdem da.
       */
      const lokalerVerlauf = rohesLokal.map(wendeWirkungenAn)

      // Augenblicke für den Funken — aus beiden Zweigen, wie jede Wirkung.
      // Eigene optimistische Zeilen nicht: die hat der Versand schon gezählt,
      // und ein gescheiterter nimmt sie dort wieder heraus.
      if (activeContact?.isFriend && currentUserId) {
        const funkenEreignisse = ereignisseAusNachrichten(
          [...lokalerVerlauf, ...processedList].filter((m) => !(m.isSelf && isOptimisticMessage(m))),
          currentUserId,
          activeContact.userId,
        )
        if (funkenEreignisse.length) {
          void useFunkenStore.getState().nimmAuf(activeContact.userId, funkenEreignisse)
        }
      }

      const nochZuTilgen = new Map<string, { msg: ChatMessage; geloeschtAm: string }>()
      for (const m of [...rohesLokal, ...processedList] as ChatMessage[]) {
        if (m.isDeleted) continue
        const loeschung = findeLoeschung(m)
        if (!loeschung) continue
        const schluessel = m.clientUuid || `#${m.id}`
        if (!nochZuTilgen.has(schluessel)) {
          nochZuTilgen.set(schluessel, { msg: m, geloeschtAm: loeschung.deletedAt })
        }
      }
      for (const { msg, geloeschtAm } of nochZuTilgen.values()) {
        void tilgeNachrichtLokal(currentMid, msg, geloeschtAm).catch(() => {})
      }

      /*
       * Die Anhänge holen und dauerhaft ablegen.
       *
       * Bis 10/2026 lag ein empfangenes Bild nur im Arbeitsspeicher und war
       * nach dem Neuladen weg — der Server behält den Blob 30 Tage, die
       * Nachricht trägt nur einen Zeiger darauf. Jetzt wird der Anhang beim
       * Empfang geholt und in der kontogebundenen Ablage abgelegt. Danach ist
       * er ohne Netz und ohne Server da.
       *
       * Der Versand ist nicht betroffen: er läuft im Hintergrund weiter, und
       * ein Anhang, der nicht zu holen war, blockiert nichts.
       */
      void sichereAnhaenge(processedList, currentMid).catch(() => {})

      setMessages((prev) => {
        const processedClientUuids = new Set<string>()
        for (const m of processedList) {
          if (m.clientUuid) processedClientUuids.add(m.clientUuid)
        }
        const pendingOptimistic = prev
          .filter((m) => m.isSelf && m.clientUuid && !processedClientUuids.has(m.clientUuid))
          .map((m) => {
            // Dieselben Wirkungen wie im lokalen Verlauf. Ohne sie brächte
            // diese Fassung eine Reaktion oder Bearbeitung weniger mit und
            // würde die berechnete beim Mischen wieder verdrängen.
            const zeile = wendeWirkungenAn(m)
            return {
              ...zeile,
              ...hakenFuer(zeile, { maxPartnerReadId, maxPartnerDeliveredId }),
            }
          })
        const systemzeilen = prev.filter((m) => m.isSystem)
        const frisch = [...processedList, ...pendingOptimistic, ...systemzeilen]
        // **Immer** zusammenführen, auch bei leerem lokalem Verlauf: nur so
        // greifen die Zusammenführungsregeln (echt schlägt Platzhalter,
        // bestätigt schlägt optimistisch) und die Sortierung ist dieselbe.
        const combined = mischeVerlauf(lokalerVerlauf, frisch, verlaufsArt) as ChatMessage[]
        sessionChatCache.set(currentMid, combined.slice(-80))
        void saveLocalMessages(currentMid, combined.slice(-200), verlaufsArt)
        return combined
      })

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

      // Ungelesenes, das dieser Stand nicht mehr öffnen kann: **eine**
      // Systemzeile je Gespräch, nicht je Umschlag. Die Kennung ist stabil,
      // damit der nächste Durchlauf sie nicht noch einmal anhängt.
      if (unlesbar > 0) {
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
        sendE2eeControlMessage({
          type: 'delivery_receipt',
          delivered_up_to_id: idToDeliver,
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
        const dispatchReadReceipt = () => {
          sendE2eeControlMessage({
            type: 'read_receipt',
            read_up_to_id: idToAck,
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
