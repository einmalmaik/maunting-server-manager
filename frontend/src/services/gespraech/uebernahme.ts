/**
 * Die Übernahme einer Lesung in den lokalen Verlauf.
 *
 * Der Block stand bis 23.10.2026 in `useNachrichtenLaden`. Herausgezogen ist
 * die Mechanik — auswerten, Wirkungen anwenden, mischen, speichern —, damit
 * auch ein Hintergrundlauf sie benutzen kann: der liest das Gerätepostfach,
 * ordnet jeden Eintrag seinem Gespraech zu und muss die Nachricht ablegen,
 * ohne dass ein Chat offen ist.
 *
 * **Was hier bleibt, ist die Wahrheit über den Verlauf.** Nach dieser Funktion
 * liegt der neue Stand in der Ablage; was die Oberflaeche daraus macht
 * (Häkchen, Quittungen, Leiste, Ungelesen-Zaehler), bleibt beim Aufrufer.
 *
 * **Die Rueckrufe sind die Grenze.** Vier davon muessen die Oberflaeche
 * erreichen, damit sie mitzieht (Partnerquittungen, Ungelesen, Verfall,
 * Systemzeile). Im Hintergrundlauf haengen sie an den Stores und die beiden
 * Anzeige-Rueckrufe bleiben leer — der naechste offene Chat liest den Verlauf
 * ohnehin aus der Ablage.
 */

import type { ChatGroupItem } from '@/api/social'
import type { ChatMessage } from '@/components/social/ChatMessageBubble'
import type { Lesung } from '@/hooks/useKonversation'
import { vergissEigeneWirkungen } from '@/services/eigeneWirkungen'
import { ereignisseAusNachrichten } from '@/services/funkenService'
import { sessionChatCache } from '@/services/klartextSpeicher'
import { sichereAnhaenge } from '@/services/medienSicherung'
import {
  entfernePlatzhalterUndDubletten,
  isOptimisticMessage,
  loadLocalMessages,
  mischeVerlauf,
  saveLocalMessages,
} from '@/services/messengerLocalStore'
import { tilgeInhalt, tilgeNachrichtLokal } from '@/services/nachrichtLoeschen'
import { wendeReaktionenAn } from '@/services/reaktionen'
import { hakenFuer } from '@/services/auswertung/quittung'
import { partnerKennungen } from '@/services/quittungsstand'
import { werteUmschlaegeAus } from '@/services/umschlagAuswertung'
import { useFunkenStore } from '@/stores/funkenStore'

export interface UebernahmeKontext {
  mailbox: string
  eigeneId: number
  contact: { userId: number; username?: string; isFriend?: boolean } | null
  group: ChatGroupItem | null
  /** Was ein Konto in dieser Gruppe darf; aus `wirksameGruppenrechte`. */
  gruppenrechteVon: (konto: number) => Set<string>
  t: (schluessel: string, werte?: Record<string, unknown>) => string
  /** Der gespeicherte Stand der Partnerquittungen — die Untergrenze der Häkchen. */
  maxPartnerReadId: number
  maxPartnerDeliveredId: number
  quittiertGelesen: (bis: number) => void
  quittiertZugestellt: (bis: number) => void
  /** Der Partner nennt die Nachrichten selbst — der genaue Weg. */
  quittiertGelesenIds?: (ids: string[]) => void
  quittiertZugestelltIds?: (ids: string[]) => void
  markAsRead: (mailbox: string) => void
  setVerfallSekunden: (sekunden: number) => void
  zeigeSystemzeile: (text: string, kennung?: string) => void
}

export interface Uebernahme {
  /** Die Zeilen aus dieser Lesung, mit Wirkungen und Häkchen. */
  processedList: ChatMessage[]
  /** Der lokale Verlauf, wie er vor dem Mischen in der Ablage lag. */
  lokalerVerlauf: ChatMessage[]
  /** Was jetzt in der Ablage steht (gemischt und gespeichert). */
  combined: ChatMessage[]
  /** Wie dieses Gespraech gemischt wird: direkt oder Gruppe. */
  verlaufsArt: 'direkt' | 'gruppe'
  /** Die Mailbox des Gespraechs (fuer den Sitzungs-Cache). */
  mailbox: string
  maxIncomingId: number
  unlesbar: number
}

/**
 * Wertet die Lesung aus und schreibt sie in den lokalen Verlauf.
 *
 * `null` heisst: der Lauf wurde mittendrin verworfen (anderes Gespraech). Dann
 * ist **nichts** geschrieben, und der Aufrufer darf den Lesestand nicht
 * uebernehmen.
 */
export async function uebernimmLesungen(
  kontext: UebernahmeKontext,
  lesungen: Lesung[],
  optionen: { istAktuell?: () => boolean } = {},
): Promise<Uebernahme | null> {
  const { mailbox: currentMid, eigeneId: currentUserId, contact: activeContact, group: activeGroup } = kontext
  const istAktuell = optionen.istAktuell ?? (() => true)

  const {
    decryptedList,
    aenderungen,
    loeschungen,
    reaktionen,
    maxPartnerReadId,
    maxPartnerDeliveredId,
    maxIncomingId,
    unlesbar,
  } = await werteUmschlaegeAus(lesungen, {
    currentMid,
    currentUserId,
    activeContact: activeContact as { userId: number; username: string } | null,
    activeGroup,
    gruppenrechteVon: kontext.gruppenrechteVon,
    t: kontext.t,
    quittiertGelesen: kontext.quittiertGelesen,
    quittiertZugestellt: kontext.quittiertZugestellt,
    quittiertGelesenIds: kontext.quittiertGelesenIds,
    quittiertZugestelltIds: kontext.quittiertZugestelltIds,
    markAsRead: kontext.markAsRead,
    setVerfallSekunden: kontext.setVerfallSekunden,
    zeigeSystemzeile: kontext.zeigeSystemzeile,
  })
  const findeAenderung = (m: ChatMessage) =>
    aenderungen.finde(m, (urheber) => urheber !== undefined && Number(urheber) === Number(m.senderId))

  /**
   * Wessen Loeschbefehl befolgt wird.
   *
   * Der eigene Absender immer. In einer Gruppe zusaetzlich, wer
   * `delete_messages` traegt — das ist seit 09/2026 auch wirklich dieses
   * Recht. Die Rechtelage kommt aus derselben Stelle wie im Rechte-Dialog,
   * inklusive der Rollen aus dem verschluesselten Block.
   *
   * Die ehrliche Grenze steht hier und nicht im Werbetext: Moderation unter
   * Ende-zu-Ende-Verschluesselung ist eine Bitte, die Clients befolgen — keine
   * Tatsache, die der Server durchsetzt.
   */
  const findeLoeschung = (m: ChatMessage) =>
    loeschungen.finde(m, (urheber) => {
      if (urheber === undefined) return false
      if (Number(urheber) === Number(m.senderId)) return true
      if (!activeGroup) return false
      return kontext.gruppenrechteVon(Number(urheber)).has('delete_messages')
    })

  /**
   * Die Wirkungen dieses Durchlaufs auf eine Zeile, die es schon gibt.
   *
   * Gebraucht an **zwei** Stellen, und genau darin lag ein Fehler: der lokale
   * Verlauf wendete Loeschung und Reaktionen an, die noch unbestaetigten
   * Zeilen aus dem React-Zustand dagegen gar nichts. `mischeVerlauf` laesst
   * bei zwei optimistischen Fassungen die aus dem Zustand gewinnen — die
   * gerade berechnete Reaktion wurde damit wieder ueberschrieben.
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

  // Was der Partner ausdrücklich bestätigt hat — einmal gelesen, nicht je
  // Zeile aus der Ablage geholt.
  const partner = partnerKennungen(currentMid)

  // Geloescht = geloescht. Kein Originaltext wird aufbewahrt (Zero Knowledge).
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
      // Kein originalText bei Loeschung — geloescht ist geloescht.
      originalText = undefined
    }

    // Dynamisches Haekchen-System, eine Stelle fuer beide Wege: gelesen vor
    // zugestellt, und der gespeicherte Stand ist die Untergrenze (`hakenFuer`).
    const { isRead, isDelivered, status } = hakenFuer(msg, {
      maxPartnerReadId,
      maxPartnerDeliveredId,
      gelesenIds: partner.gelesen,
      zugestelltIds: partner.zugestellt,
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
      reaktionen: wendeReaktionenAn(msg.reaktionen, reaktionen.finde(msg)),
    }
    // Ausblenden reicht nicht: was hier stehen bleibt, schreibt
    // `saveLocalMessages` gleich wieder auf die Platte — Text und Anhang
    // einer geloeschten Nachricht eingeschlossen.
    return isDeleted && deletedAt ? tilgeInhalt(fertig, deletedAt) : fertig
  })

  const verlaufsArt = activeGroup ? ('gruppe' as const) : ('direkt' as const)
  await entfernePlatzhalterUndDubletten(currentMid, verlaufsArt).catch(() => ({}))
  if (!istAktuell()) return null

  const rohesLokal = await loadLocalMessages(currentMid)
  if (!istAktuell()) return null

  /**
   * Eine Loeschung, die hier ankommt, gilt auch fuer das, was schon auf der
   * Platte liegt. Ohne diesen Durchgang blieben zwei Fassungen zurueck: die
   * Zeile im eigenen Verlauf mit Text und Anhang, und der abgelegte
   * Umschlag-Klartext.
   */
  const lokalerVerlauf = rohesLokal.map(wendeWirkungenAn)

  // Augenblicke fuer den Funken — aus beiden Zweigen, wie jede Wirkung. Eigene
  // optimistische Zeilen nicht: die hat der Versand schon gezaehlt.
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

  // Die Anhaenge holen und dauerhaft ablegen. Ein Anhang, der nicht zu holen
  // war, blockiert nichts.
  void sichereAnhaenge(processedList, currentMid).catch(() => {})

  /*
   * Der Häkchenstand der eigenen Zeilen zieht mit.
   *
   * Er kommt aus den Partnerquittungen, nicht aus dem Umschlag dieses Laufs:
   * eine Zustellquittung ist ein eigenes Steuerpaket und steht in einer
   * anderen Zeile. Bis 23.10.2026 rechnete nur die Anzeige darüber
   * (`anzeigeVerlauf`); gespeichert blieb der Stand vom Senden. Nach einem
   * Neustart war das Häkchen dann weg, bis der nächste Lauf es neu ergab.
   */
  const lokalerVerlaufMitHaken = lokalerVerlauf.map((m) =>
    m.isSelf
      ? {
          ...m,
          ...hakenFuer(m, {
            maxPartnerReadId,
            maxPartnerDeliveredId,
            gelesenIds: partner.gelesen,
            zugestelltIds: partner.zugestellt,
          }),
        }
      : m,
  )

  // **Immer** zusammenfuehren, auch bei leerem lokalem Verlauf: nur so greifen
  // die Zusammenfuehrungsregeln und die Sortierung ist dieselbe.
  const combined = mischeVerlauf(lokalerVerlaufMitHaken, processedList, verlaufsArt) as ChatMessage[]
  sessionChatCache.set(currentMid, combined.slice(-80))
  await saveLocalMessages(currentMid, combined.slice(-200), verlaufsArt)

  // Die aufbewahrten Wirkungen der eigenen Zweitgeraete fallen erst jetzt: der
  // neue Stand ist geschrieben, ein zweiter Durchlauf wendet dieselbe Wirkung
  // idempotent noch einmal an.
  await vergissEigeneWirkungen(currentMid).catch(() => {})

  return { processedList, lokalerVerlauf: lokalerVerlaufMitHaken, combined, verlaufsArt, mailbox: currentMid, maxIncomingId, unlesbar }
}

/**
 * Der Verlauf, wie ihn die Oberflaeche zeigt.
 *
 * Die Ablage kennt nur, was feststeht. Im Fenster stehen zusaetzlich die
 * eigenen, noch unbestaetigten Zeilen und die Systemzeilen dieser Sitzung —
 * beide gehoeren in dieselbe Mischung, sonst gewaenne beim Zusammenfuehren
 * die falsche Fassung.
 */
export function anzeigeVerlauf(
  uebernahme: Uebernahme,
  bisher: readonly ChatMessage[],
  maxPartnerReadId: number,
  maxPartnerDeliveredId: number,
): ChatMessage[] {
  const processedClientUuids = new Set<string>()
  for (const m of uebernahme.processedList) {
    if (m.clientUuid) processedClientUuids.add(m.clientUuid)
  }
  const partner = partnerKennungen(uebernahme.mailbox)
  const pendingOptimistic = bisher
    .filter((m) => m.isSelf && m.clientUuid && !processedClientUuids.has(m.clientUuid))
    .map((m) => ({
      ...m,
      ...hakenFuer(m, {
        maxPartnerReadId,
        maxPartnerDeliveredId,
        gelesenIds: partner.gelesen,
        zugestelltIds: partner.zugestellt,
      }),
    }))
  const systemzeilen = bisher.filter((m) => m.isSystem)
  const frisch = [...uebernahme.processedList, ...pendingOptimistic, ...systemzeilen]
  // **Immer** zusammenfuehren, auch bei leerem lokalem Verlauf: nur so greifen
  // die Zusammenfuehrungsregeln und die Sortierung ist dieselbe.
  const combined = mischeVerlauf(uebernahme.lokalerVerlauf, frisch, uebernahme.verlaufsArt) as ChatMessage[]
  sessionChatCache.set(uebernahme.mailbox, combined.slice(-80))
  return combined
}
