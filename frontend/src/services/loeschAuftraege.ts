/**
 * Der dauerhafte Auftrag zum Server-Aufräumen einer gelöschten Nachricht.
 *
 * Das lokale Löschen hängt seit 10/2026 nicht mehr am Server: es ist vollzogen,
 * auch wenn das Netz weg ist. Was danach kommt — Chiffretext und Anhänge vom
 * Server nehmen — war bis hierher ein einzelner Best-Effort-Aufruf. Scheiterte
 * er, blieb die Kopie bis zum Ablauf ihrer Frist liegen und niemand versuchte
 * es erneut. Der Plan (§10) verlangt dafür einen eigenen dauerhaften Auftrag.
 *
 * **Warum diese Ablage?** Der Auftrag nennt Mailbox, logische und Umschlag-
 * Kennung sowie die Medienkennungen samt Bindung — also dieselben Angaben wie
 * der Verlauf daneben. Er liegt deshalb in der kontogebundenen, versiegelten
 * Messenger-Ablage (`messengerLocalStore`, Store `loesch_auftraege`) und nicht
 * im localStorage: dort wäre er für jedes Konto des Browsers lesbar, und ein
 * Kontowechsel müsste ihn an einer zweiten Stelle aufräumen. Eine eigene
 * IndexedDB wäre ein dritter Ort derselben Art.
 *
 * **Was hier nicht steht:** der Klartext der Nachricht. Der Auftrag braucht nur
 * die Kennungen; der Inhalt ist lokal längst Grabstein.
 *
 * Diese Datei importiert nichts. Sie ist der gemeinsame Wortschatz von Ablage,
 * Anlegen und Abarbeiten und damit frei von Zyklen.
 */

/** Ein Anhang, der beim Löschen vom Server verschwinden soll. */
export interface LoeschMedienbindung {
  mediaId: string
  /** Wer den Blob hochgeladen hat; nur er darf ihn löschen. */
  absenderId: number
  /** Das Gespräch, zu dem der Anhang gehört. */
  mailbox: string
}

/**
 * Ein eingereihter Auftrag, so wie er in der Ablage liegt.
 *
 * Lesbar bleiben nur die Felder mit Index (`id`, `mailbox`, die Termine).
 * Medienkennungen und Nachrichtenkennung liegen versiegelt daneben wie der
 * Verlauf; sie sind zwar keine Inhalte, gehören aber zum selben Gespräch.
 */
export interface LoeschAuftrag {
  /** `${mailbox}:${logische Kennung}:${fremd|eigen}` — eine Zeile je Nachricht. */
  id: string
  mailbox: string
  /** Die logische Kennung (`client_uuid`), unter der die Umschläge liegen. */
  clientUuid: string | null
  /** Die Umschlagkennung; bei einer Nachricht ohne logische Kennung die einzige. */
  nachrichtId: number
  /** Die Blobs dieser Nachricht — die Zeile im Verlauf ist nur noch Grabstein. */
  medien: LoeschMedienbindung[]
  /** `true`: Moderation einer fremden Nachricht. Der Blob darf bleiben. */
  fremd: boolean
  angelegtAm: number
  versuche: number
  naechsterVersuch: number
}

/** Nach so vielen Versuchen fällt der Auftrag — sichtbar, nicht still. */
export const MAX_VERSUCHE = 6

/**
 * So viele offene Aufträge hält die Warteschlange.
 *
 * Der Auftrag verschwindet mit dem Erfolg, in der Regel also sofort. Die Grenze
 * greift nur, wenn sehr viele Löschungen hintereinander ohne Netz anfallen.
 * Ist sie erreicht, nimmt die Ablage nichts mehr an und der Aufrufer versucht
 * es einmal direkt; ältere Aufträge still zu verdrängen wäre genau das stille
 * Aufgeben, gegen das diese Warteschlange geschrieben ist.
 */
export const LOESCH_AUFTRAEGE_MAX = 500

/**
 * Der Rückzug zwischen zwei Versuchen, in Millisekunden.
 *
 * Der erste Anlauf ist der unmittelbare beim Löschen; danach wird der Abstand
 * größer. Kein Dauerfeuer gegen ein totes Netz, aber auch kein Aufgeben nach
 * einem Abend offline.
 */
const RUECKZUG_MS = [30_000, 2 * 60_000, 10 * 60_000, 60 * 60_000, 6 * 60 * 60_000]

export function loeschRueckzugMs(versuche: number): number {
  const stelle = Math.max(0, Math.min(RUECKZUG_MS.length - 1, Math.floor(versuche)))
  return RUECKZUG_MS[stelle]
}

/**
 * Diese Status sind ein Urteil, kein Ausfall.
 *
 * 400 (unbrauchbar), 403 (kein Zugang), 404 (schon weg), 410 (Ziel ist weg)
 * und 422 (Schema) ändern sich durch Warten nicht. Alles andere — Netz, 401
 * nach abgelaufener Sitzung, 409, 429, 5xx — bleibt offen und wird erneut
 * versucht. `404` ist hier kein Fehler im engeren Sinn: der Server antwortet
 * idempotent, und eine bereits entfernte Kopie ist das Ziel.
 */
const ENDGUELTIG = new Set([400, 403, 404, 410, 422])

export function istEndgueltigAbgelehnt(fehler: unknown): boolean {
  const status = Number((fehler as { status?: unknown } | null)?.status)
  return Number.isInteger(status) && ENDGUELTIG.has(status)
}
