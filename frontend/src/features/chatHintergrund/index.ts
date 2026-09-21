/**
 * Der Chat-Hintergrund — ein Modul für alle Unterhaltungsflächen.
 *
 * Hier liegt alles, was einen Hintergrund ausmacht: der Katalog der Vorlagen,
 * die Schicht, die ihn zeichnet, das Fenster, in dem er gewählt wird, und der
 * Speicher dahinter. Messenger und KI-Bereich holen sich von hier dasselbe;
 * keiner von beiden hat eine eigene Fassung.
 *
 * Eine Fläche einbauen heisst:
 *
 * ```tsx
 * <section className="relative …">        // der Kasten braucht `relative`
 *   <ChatHintergrund bereich="ki" />      // die Schicht, immer zuerst
 *   <div className="relative …">…</div>   // alles Sichtbare darüber
 * </section>
 * ```
 *
 * und irgendwo einen Einstieg: `<ChatHintergrundKnopf bereich="ki" />` oder,
 * wo das Haus eine eigene Menüzeile hat, `ChatHintergrundDialog` direkt.
 */
export {
  CHAT_HINTERGRUND_EVENT,
  STANDARD_HINTERGRUND,
  beobachteChatHintergrund,
  hintergrundSchluessel,
  ladeChatHintergrund,
  speichereChatHintergrund,
  type ChatHintergrundBereich,
  type ChatHintergrundKonfiguration,
} from './speicher'

export { ChatHintergrund, HintergrundFlaeche, useChatHintergrund } from './ChatHintergrund'
export { ChatHintergrundDialog, type ChatHintergrundDialogProps } from './ChatHintergrundDialog'
export { ChatHintergrundKnopf } from './ChatHintergrundKnopf'
export {
  HINTERGRUND_VORLAGEN,
  STANDARD_VORLAGE,
  vorlageMitId,
  type HintergrundId,
  type HintergrundVorlage,
  type HintergrundVorlageId,
} from './vorlagen'
