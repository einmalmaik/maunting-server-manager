/**
 * Entschlüsseltes im Arbeitsspeicher: Verläufe je Mailbox und Anhänge je
 * Medienkennung.
 *
 * Beides liegt bewusst nur hier und nie im `localStorage`. Es beschleunigt den
 * Wechsel zwischen Chats und das erneute Anzeigen eines Bildes.
 *
 * Bis 09/2026 standen die beiden Speicher in `Messenger.tsx` und
 * `ChatMediaAttachments.tsx`. Weder das Abmelden noch die Messenger-Sperre kam
 * an sie heran: der Klartext blieb bis zum Neuladen des Tabs liegen, auch unter
 * dem Sperrschirm und nach dem Wechsel zu einem anderen Konto.
 */

import type { ChatMessage } from '@/components/social/ChatMessageBubble'
import { leereSuchspeicher } from './verlaufSuche'

/** Die letzten Nachrichten je Mailbox, für den sofortigen Chatwechsel. */
export const sessionChatCache = new Map<string, ChatMessage[]>()

/** Entschlüsselte Anhänge als data-URL, je Medienkennung. */
export const chatMediaBlobCache = new Map<string, string>()

/** Wirft allen Klartext im Arbeitsspeicher weg. Gehört an jedes Abmelden und jedes Sperren. */
export function leereKlartextSpeicher(): void {
  sessionChatCache.clear()
  chatMediaBlobCache.clear()
  leereSuchspeicher()
}
