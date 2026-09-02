/**
 * Aufträge abholen und Ergebnisse melden.
 *
 * Die Richtung ist umgedreht: das Panel kann diesen Rechner nicht anrufen,
 * also fragt der Rechner. Gefragt wird aus der Weboberfläche und nicht aus
 * Rust, weil das Zugangstoken hier liegt und ausschließlich hier liegen soll
 * (`transport.ts`) — ein zweiter Ort dafür wäre ein zweites Risiko.
 */
import { api, SanitizedApiError } from '@/api/client'

export interface Auftrag {
  id: string
  tool_name: string
  arguments: Record<string, unknown>
}

/** Der nächste Auftrag — oder `null`, wenn gerade nichts ansteht (204). */
export async function naechsterAuftrag(): Promise<Auftrag | null> {
  const antwort = await api<Auftrag | Record<string, never>>('/desktop/jobs/next')
  // `api()` übersetzt 204 in ein leeres Objekt — hier heißt das: nichts zu tun.
  return antwort && 'id' in antwort ? (antwort as Auftrag) : null
}

export async function ergebnisMelden(
  jobId: string,
  ok: boolean,
  ergebnis: Record<string, unknown>,
  errorCode?: string,
): Promise<void> {
  try {
    await api(`/desktop/jobs/${encodeURIComponent(jobId)}/result`, {
      method: 'POST',
      body: JSON.stringify({ ok, ergebnis, error_code: errorCode ?? null }),
    })
  } catch (fehler) {
    // Ein verlorenes Ergebnis ist kein Grund, die Schleife zu beenden: der
    // Auftrag verfällt dann panelseitig, und das Modell erfährt genau das
    // statt Stille. Ein 404 heißt, dass ihn jemand schon geschlossen hat.
    if (fehler instanceof SanitizedApiError && fehler.status === 404) {
      return
    }
    throw fehler
  }
}
