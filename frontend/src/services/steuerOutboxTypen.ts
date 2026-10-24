/**
 * Der dauerhafte Auftrag hinter einer Wirkung — Typen und Grenzen.
 *
 * Bearbeiten, Löschen, Reaktion, Anheften und Verfall reisten bis 10/2026 als
 * ein einziger `relayE2eeEnvelope`-Aufruf. Scheiterte der, nahm der Aufrufer
 * die lokale Wirkung zurück und zeigte einen Toast: offline war die Wirkung
 * weg, und die Gegenseite erfuhr nie davon. Der Plan (§6 Senden, §7 letzter
 * Absatz) verlangt dafür dieselbe zuverlässige Outbox wie für die
 * Schlüsselzustellung: Wirkung und Erledigtmarke gemeinsam, Wiederholung bei
 * Start, `online` und Entsperren, kein stilles Aufgeben.
 *
 * **Was hier liegt, ist der Inhalt der Nutzlast — nicht der fertige Umschlag.**
 * Ein Steuerumschlag ist je Zielgerät ein eigener Hybridumschlag, und in einer
 * Gruppe hängt er am aktuellen Gruppenschlüssel. Beides ändert sich zwischen
 * zwei Versuchen: ein Gerät kommt dazu, ein anderes wird entfernt, die
 * Mitgliedschaft rotiert den Schlüssel. Ein abgelegter Chiffretext wäre beim
 * zweiten Anlauf veraltet und ginge an Geräte, die es so nicht mehr gibt. Die
 * rohen Felder dagegen sind das, was der Absender sagen wollte; der Lauf baut
 * daraus mit **frisch aufgelösten** Zielgeräten neue Umschläge und signiert
 * erneut. Die Kennung bleibt dabei dieselbe — sie ist der `client_uuid` des
 * Pakets, und daran erkennt das Relais eine Wiederholung.
 *
 * Diese Datei importiert nichts. Sie ist der gemeinsame Wortschatz von Ablage,
 * Einreihen und Abarbeiten und damit frei von Zyklen.
 */

/** Wohin die Wirkung gehört: ein Direktchat oder eine Gruppe. */
export type SteuerArt = 'direkt' | 'gruppe'

/** Der Nachrichtenstand, den eine Bearbeitung vorfindet und hinterlässt. */
export interface SteuerNachrichtFassung {
  text: string
  isEdited: boolean
  editedAt?: string
  originalText?: string
}

/**
 * Wie eine endgültig abgewiesene Wirkung lokal zurückgenommen wird.
 *
 * Nur die Angaben, die dafür nötig sind, und immer mit **beiden** Ständen:
 * `vorher` ist das, was wiederhergestellt wird, `nachher` das, was diese
 * Wirkung gesetzt hat. Der Lauf nimmt nur zurück, solange der lokale Stand
 * noch `nachher` ist — hat der Mensch inzwischen etwas anderes getan, gilt
 * seins und nicht die Rücknahme einer alten Warteschlange.
 *
 * Eine Löschung trägt `keine`: sie ist lokal schon vollzogen, und eine
 * Löschung lässt sich nicht zurücknehmen.
 */
export type SteuerRuecknahme =
  | { art: 'keine' }
  | {
      art: 'reaktion'
      mailbox: string
      ziel: string | null
      zielId: number | null
      vorher: Record<string, number[]>
      /** `undefined` heißt: die letzte Reaktion wurde genommen. */
      nachher: Record<string, number[]> | undefined
    }
  | {
      art: 'bearbeitung'
      mailbox: string
      ziel: string | null
      zielId: number | null
      vorher: SteuerNachrichtFassung
      nachher: SteuerNachrichtFassung
    }
  | {
      art: 'anheftung'
      mailbox: string
      vorher: { clientUuid: string; stand: string }
      nachher: { clientUuid: string; stand: string }
    }
  | {
      art: 'verfall'
      mailbox: string
      vorher: { sekunden: number; stand: string }
      nachher: { sekunden: number; stand: string }
    }

/**
 * Ein eingereihter Auftrag, so wie er in der Ablage liegt.
 *
 * `id` ist zugleich `client_uuid` des Steuerpakets und ändert sich nie. Nur die
 * Felder hier sind lesbar (sie tragen die Indizes); alles, was den Inhalt
 * verrät — die Nutzlast, die Mitgliederliste, die Rücknahme —, liegt versiegelt
 * daneben wie der Verlauf.
 */
export interface SteuerAuftrag {
  id: string
  mailbox: string
  art: SteuerArt
  /** Gegenüber im Direktchat, `null` in der Gruppe. */
  peerId: number | null
  groupId: number | null
  /** Die Mitgliedschaft, wie sie beim Einreihen galt; der Lauf holt sie frisch. */
  mitglieder: number[]
  istEigentuemer: boolean
  typ: string
  /** `client_uuid` der betroffenen Nachricht, für das Aufräumen beim Löschen. */
  ziel: string | null
  zielId: number | null
  /** Die Rohfelder der Nutzlast, ohne Beleg und ohne die abgeleiteten Felder. */
  felder: Record<string, unknown>
  ruecknahme?: SteuerRuecknahme
  angelegtAm: number
  versuche: number
  naechsterVersuch: number
}

/** Was ein Aufrufer einreihen möchte — die Ablage füllt den Rest auf. */
export interface SteuerEintrag {
  typ: string
  felder: Record<string, unknown>
  /** `client_uuid` der Nachricht, auf die sich die Wirkung bezieht. */
  ziel?: string | null
  zielId?: number | null
  ruecknahme?: SteuerRuecknahme
}

/** Nach so vielen Versuchen fällt ein Auftrag — sichtbar, nicht still. */
export const MAX_VERSUCHE = 6

/**
 * So viele Aufträge hält die Warteschlange.
 *
 * Eine Woche offline erzeugt ein paar Dutzend Wirkungen, keine tausend. Ist die
 * Grenze erreicht, nimmt `legeSteuerAuftragAn` **nichts** mehr an und der
 * Aufrufer erfährt es: die Wirkung wird zurückgenommen und gemeldet. Ältere
 * Aufträge still zu verdrängen wäre genau das stille Aufgeben, das diese Outbox
 * verhindern soll.
 */
export const MAX_AUFTRAEGE = 200

/**
 * Der Rückzug zwischen zwei Versuchen, in Millisekunden.
 *
 * Der erste Anlauf ist der unmittelbare beim Einreihen; danach wird der Abstand
 * größer. Kein Dauerfeuer: ein Gerät, das drei Tage offline war, soll nicht im
 * Takt des offenen Chats gegen ein totes Netz klopfen. Nach dem letzten Versuch
 * fällt der Auftrag und die Oberfläche sagt es.
 */
const RUECKZUG_MS = [30_000, 2 * 60_000, 10 * 60_000, 60 * 60_000, 6 * 60 * 60_000]

export function steuerRueckzugMs(versuche: number): number {
  const stelle = Math.max(0, Math.min(RUECKZUG_MS.length - 1, Math.floor(versuche)))
  return RUECKZUG_MS[stelle]
}

/**
 * Diese Status sind ein Urteil, kein Ausfall.
 *
 * 400 (unbrauchbar), 403 (kein Zugang), 404 (Ziel gibt es nicht), 410 (Ziel ist
 * weg) und 422 (Schema) ändern sich durch Warten nicht. Alles andere — Netz,
 * 401 nach abgelaufener Sitzung, 409, 429, 5xx — bleibt offen und wird erneut
 * versucht.
 */
const ENDGUELTIG = new Set([400, 403, 404, 410, 422])

export function istEndgueltigAbgelehnt(fehler: unknown): boolean {
  const status = Number((fehler as { status?: unknown } | null)?.status)
  return Number.isInteger(status) && ENDGUELTIG.has(status)
}

/** Der Server hat die Wirkung abgelehnt; sie wird nicht wiederholt. */
export class SteuerAbgewiesenError extends Error {
  readonly status: number | null

  constructor(status: number | null) {
    super('Der Server hat die Änderung abgelehnt.')
    this.name = 'SteuerAbgewiesenError'
    this.status = status
  }
}

/** Die Warteschlange ist voll; die Wirkung wurde nicht angenommen. */
export class SteuerOutboxVollError extends Error {
  constructor() {
    super('Die Warteschlange für Änderungen ist voll.')
    this.name = 'SteuerOutboxVollError'
  }
}
