/**
 * Suchen, was schon da ist.
 *
 * **Kein neuer Index.** Mit gesetztem Messenger-PIN liegen die Zeilen
 * versiegelt in der IndexedDB. Ein Index über den Text wäre dann entweder
 * nutzlos (weil er nichts sieht) oder ein Loch (weil er im Klartext danebenläge
 * und genau das verriete, was das Siegel verbirgt). `loadLocalMessages`
 * entsiegelt eine Mailbox ohnehin vollständig — also wird durchgesehen.
 *
 * **Gesperrt wird nicht gesucht.** Ohne Schlüssel gibt die Ablage nichts
 * heraus. Das Ergebnis sagt das dann auch, statt leere Treffer zu zeigen und
 * den Eindruck zu erwecken, es gäbe nichts.
 *
 * Für die Sitzung wird das Entsiegelte im Arbeitsspeicher gehalten, damit das
 * Tippen nicht bei jedem Buchstaben die ganze Platte anfasst. Beim Sperren
 * wirft `leereSuchspeicher()` es weg.
 */

import { istOffen, siegelAktiv } from './lokaleVersiegelung'
import {
  listeLokaleMailboxen,
  loadLocalMessages,
  type LocalStoredMessage,
} from './messengerLocalStore'

export interface Treffer {
  blindMailboxId: string
  id: number
  clientUuid?: string
  senderId: number
  senderName?: string
  isSelf: boolean
  createdAt: string
  /** Der Textausschnitt um den Treffer, mit den Randmarken `…`. */
  auszug: string
  /** Wo im Auszug der Treffer sitzt, für die Hervorhebung. */
  von: number
  bis: number
}

export interface Suchergebnis {
  treffer: Treffer[]
  /** `true`, wenn nicht gesucht werden konnte, weil der Messenger zu ist. */
  gesperrt: boolean
}

/** So viel Text steht links und rechts vom Treffer. */
const RAND = 40

/** Mehr als das liest niemand durch; der Rest wäre nur Arbeit. */
export const TREFFER_MAX = 200

/** Entsiegelt je Mailbox, für die Dauer dieser Sitzung. */
const speicher = new Map<string, LocalStoredMessage[]>()

/** Wirft den Zwischenspeicher weg. Gehört an jedes Sperren. */
export function leereSuchspeicher(): void {
  speicher.clear()
}

/** Merkt vor, dass sich eine Mailbox geändert hat. */
export function vergissMailbox(blindMailboxId: string): void {
  speicher.delete(blindMailboxId)
}

async function hole(blindMailboxId: string): Promise<LocalStoredMessage[]> {
  const da = speicher.get(blindMailboxId)
  if (da) return da
  const gelesen = await loadLocalMessages(blindMailboxId)
  speicher.set(blindMailboxId, gelesen)
  return gelesen
}

/** Der durchsuchbare Text einer Zeile — auch Anhänge haben Namen und Titel. */
function suchtext(m: LocalStoredMessage): string {
  const teile = [m.text]
  const note = m.noteAttachment as { title?: string; content?: string } | undefined
  if (note) teile.push(note.title || '', note.content || '')
  const cal = m.calendarAttachment as { title?: string; description?: string; location?: string } | undefined
  if (cal) teile.push(cal.title || '', cal.description || '', cal.location || '')
  const datei = m.fileAttachment as { name?: string } | undefined
  if (datei) teile.push(datei.name || '')
  const bild = m.imageAttachment as { name?: string } | undefined
  if (bild) teile.push(bild.name || '')
  return teile.filter(Boolean).join(' ')
}

function baueTreffer(m: LocalStoredMessage, mid: string, stelle: number, laenge: number, text: string): Treffer {
  const von = Math.max(0, stelle - RAND)
  const bis = Math.min(text.length, stelle + laenge + RAND)
  const auszug = (von > 0 ? '…' : '') + text.slice(von, bis) + (bis < text.length ? '…' : '')
  const versatz = von > 0 ? 1 : 0
  return {
    blindMailboxId: mid,
    id: m.id,
    clientUuid: m.clientUuid,
    senderId: m.senderId,
    senderName: m.senderName,
    isSelf: m.isSelf,
    createdAt: m.createdAt,
    auszug,
    von: stelle - von + versatz,
    bis: stelle - von + laenge + versatz,
  }
}

/**
 * Sucht in einem Chat. Das Neueste zuerst — danach sucht man meistens.
 *
 * Gelöschte Zeilen bleiben außen vor: an einem Grabstein ist nichts zu finden,
 * und ein Treffer auf „Diese Nachricht wurde gelöscht" wäre grotesk.
 */
export async function sucheImChat(blindMailboxId: string, frage: string): Promise<Suchergebnis> {
  const gesucht = frage.trim().toLowerCase()
  if (!gesucht || !blindMailboxId) return { treffer: [], gesperrt: false }
  if (siegelAktiv() && !istOffen()) return { treffer: [], gesperrt: true }

  const zeilen = await hole(blindMailboxId)
  const treffer: Treffer[] = []
  for (const m of zeilen) {
    if (m.isDeleted || m.isSystem) continue
    const text = suchtext(m)
    const stelle = text.toLowerCase().indexOf(gesucht)
    if (stelle < 0) continue
    treffer.push(baueTreffer(m, blindMailboxId, stelle, gesucht.length, text))
  }
  treffer.sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : b.id - a.id))
  return { treffer: treffer.slice(0, TREFFER_MAX), gesperrt: false }
}

export interface ChatTreffer {
  blindMailboxId: string
  treffer: Treffer[]
}

/**
 * Sucht über alle Chats, die auf diesem Gerät liegen.
 *
 * Gruppiert nach Chat, innerhalb eines Chats das Neueste zuerst, und die Chats
 * nach ihrem jüngsten Treffer.
 */
export async function sucheUeberall(frage: string): Promise<{ chats: ChatTreffer[]; gesperrt: boolean }> {
  const gesucht = frage.trim()
  if (!gesucht) return { chats: [], gesperrt: false }
  if (siegelAktiv() && !istOffen()) return { chats: [], gesperrt: true }

  const mailboxen = await listeLokaleMailboxen()
  const chats: ChatTreffer[] = []
  for (const mid of mailboxen) {
    const { treffer } = await sucheImChat(mid, gesucht)
    if (treffer.length) chats.push({ blindMailboxId: mid, treffer })
  }
  chats.sort((a, b) => (a.treffer[0].createdAt < b.treffer[0].createdAt ? 1 : -1))
  return { chats, gesperrt: false }
}

/**
 * Alles, was mit Sternchen markiert ist — über alle Chats.
 *
 * Dieselbe Durchsicht wie die Suche, nur mit einer anderen Frage. Deshalb
 * steht es hier und nicht in einem eigenen Modul.
 */
export async function sammleMarkierte(): Promise<{ chats: ChatTreffer[]; gesperrt: boolean }> {
  if (siegelAktiv() && !istOffen()) return { chats: [], gesperrt: true }
  const mailboxen = await listeLokaleMailboxen()
  const chats: ChatTreffer[] = []
  for (const mid of mailboxen) {
    const zeilen = await hole(mid)
    const treffer = zeilen
      .filter((m) => m.istMarkiert && !m.isDeleted && !m.isSystem)
      .map((m) => baueTreffer(m, mid, 0, 0, suchtext(m)))
    if (treffer.length) {
      treffer.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
      chats.push({ blindMailboxId: mid, treffer })
    }
  }
  chats.sort((a, b) => (a.treffer[0].createdAt < b.treffer[0].createdAt ? 1 : -1))
  return { chats, gesperrt: false }
}

/**
 * Wo ich erwähnt wurde und wo jemand auf mich geantwortet hat.
 *
 * In einer Gruppe ist das der Unterschied zwischen „irgendwann lese ich das
 * nach" und „ich weiß, wo ich gebraucht werde". Dieselbe Durchsicht, dritte
 * Frage.
 *
 * Die Rechteprüfung für `@everyone` kann hier nicht stattfinden — dafür
 * bräuchte es die Gruppenantwort, und die hängt am Netz. Der Aufrufer gibt
 * deshalb mit, welche Nachricht als Erwähnung zählt.
 */
export async function sammleAnMich(
  eigeneId: number,
  gemeint: (m: LocalStoredMessage, mid: string) => boolean,
): Promise<{ chats: ChatTreffer[]; gesperrt: boolean }> {
  if (!eigeneId) return { chats: [], gesperrt: false }
  if (siegelAktiv() && !istOffen()) return { chats: [], gesperrt: true }
  const mailboxen = await listeLokaleMailboxen()
  const chats: ChatTreffer[] = []
  for (const mid of mailboxen) {
    const zeilen = await hole(mid)
    const treffer = zeilen
      .filter((m) => !m.isDeleted && !m.isSystem && !m.isSelf && gemeint(m, mid))
      .map((m) => baueTreffer(m, mid, 0, 0, suchtext(m)))
    if (treffer.length) {
      treffer.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
      chats.push({ blindMailboxId: mid, treffer })
    }
  }
  chats.sort((a, b) => (a.treffer[0].createdAt < b.treffer[0].createdAt ? 1 : -1))
  return { chats, gesperrt: false }
}
