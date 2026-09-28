/**
 * Regeln für Benutzernamen — die Kopie von `backend/schemas/user.py`
 * (`BENUTZERNAME_MUSTER`, `BENUTZERNAME_GESPERRT`). Entscheiden tut das
 * Backend; hier steht sie nur, damit das Feld den Fehler zeigt, bevor jemand
 * auf Speichern drückt.
 *
 * Dieselbe Zeichenmenge wie die Erwähnungen (`services/erwaehnungen.ts`),
 * sonst ließe sich ein Name nicht mit @ ansprechen. Kein @, damit niemand
 * wieder eine E-Mail als Namen bekommt.
 */
export const BENUTZERNAME_MUSTER = /^[A-Za-z0-9_][A-Za-z0-9_.-]{1,30}[A-Za-z0-9_]$/

const GESPERRT = new Set(['everyone', 'here', 'alle'])

/** Vorläufiger Name ohne Grundlage (`user_<hex8>`), kein brauchbarer Vorschlag. */
const PLATZHALTER = /^user_[0-9a-f]{8}$/

export type BenutzernameFehler =
  | { schluessel: 'benutzername.fehlerForm' }
  | { schluessel: 'benutzername.fehlerReserviert'; name: string }

export function benutzernameFehler(name: string): BenutzernameFehler | null {
  const sauber = name.trim()
  if (!BENUTZERNAME_MUSTER.test(sauber)) return { schluessel: 'benutzername.fehlerForm' }
  if (GESPERRT.has(sauber.toLowerCase())) return { schluessel: 'benutzername.fehlerReserviert', name: sauber }
  return null
}

/**
 * Die Namen, die das Panel bis 09/2026 aus der E-Mail machte: die ganze
 * Adresse ohne Sonderzeichen (`namegmailcom`, Google) oder der Teil vor dem @.
 * Bei Doppelungen hing `_<n>` dahinter.
 */
function ausDerMail(name: string, email: string): boolean {
  const ohneZaehler = name.replace(/_\d+$/, '')
  const saeubern = (s: string) => s.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 48)
  return ohneZaehler === saeubern(email) || ohneZaehler === saeubern(email.split('@')[0])
}

/**
 * Womit das Wahlfeld startet: der vorläufige Name. Ausgenommen sind der
 * Platzhalter und ein Name, der alt aus der E-Mail gebildet wurde, denn den
 * soll die Wahl gerade ersetzen.
 */
export function benutzernameVorschlag(vorlaeufig: string | undefined, email?: string | null): string {
  if (!vorlaeufig || PLATZHALTER.test(vorlaeufig)) return ''
  if (email && ausDerMail(vorlaeufig, email)) return ''
  return vorlaeufig
}
