/**
 * Ein Entwurf für das Eingabefeld des KI-Chats, etwa „Seite an Singra
 * übergeben“ aus dem Secure Browser. Der Chat nimmt ihn beim Öffnen oder,
 * wenn er schon offen ist, auf das Ereignis hin; abgeschickt wird erst, wenn
 * der Nutzer selbst sendet.
 *
 * Der Entwurf liegt nur bis zum Abholen im Speicher. Ist der Chat noch nicht
 * geladen, wartet er hier, statt dass ein Ereignis ins Leere geht.
 */
export const AI_ENTWURF_EVENT = 'msm:ai-entwurf'

let wartend: string | null = null

export function entwurfUebergeben(text: string): void {
  wartend = text
  window.dispatchEvent(new CustomEvent(AI_ENTWURF_EVENT))
}

/** Gibt den wartenden Entwurf einmal heraus. */
export function entwurfNehmen(): string | null {
  const text = wartend
  wartend = null
  return text
}
