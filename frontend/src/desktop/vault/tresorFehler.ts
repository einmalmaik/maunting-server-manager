/**
 * Was ein Fehler im Tresor der Oberfläche sagen darf.
 *
 * Bis 10/2026 landete in den Toasts des Tresors `err.message`, gleich woher:
 * deutsche Technikmeldungen aus dem Code („Chunk hat die falsche Länge“) und
 * englische des Browsers („Failed to fetch“). Durch geht nur, was für die
 * Oberfläche gebaut ist: ein `TresorFehler` mit übersetztem Text oder ein
 * `SanitizedApiError`, dessen Text das Backend bereinigt hat. Alles andere
 * bekommt den Text der Aktion.
 */
import { SanitizedApiError } from '@/api/client'

/** Ein Fehler, dessen Text schon übersetzt für die Oberfläche ist: `new TresorFehler(i18n.t(…))`. */
export class TresorFehler extends Error {
  constructor(text: string) {
    super(text)
    this.name = 'TresorFehler'
  }
}

/** Der Text für einen Toast: der eigene, wenn er für die Oberfläche gebaut ist, sonst `ersatz`. */
export function fehlerText(err: unknown, ersatz: string): string {
  return (err instanceof TresorFehler || err instanceof SanitizedApiError) && err.message ? err.message : ersatz
}
