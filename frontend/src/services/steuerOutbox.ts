/**
 * Eine Steuerwirkung einreihen und, wenn sie endgültig scheitert, zurücknehmen.
 *
 * Der Weg eines Steuerpakets ist seit 10/2026 zweistufig:
 *
 * 1. **Sofort versuchen.** Ist das Netz da, verhält sich alles wie vorher —
 *    die Wirkung ist mit demselben Aufruf draußen. Erst damit bleibt der
 *    Messenger bei offener Verbindung so schnell wie zuvor.
 * 2. **Sonst einreihen.** Ein Netzfehler, ein 429 oder ein 5xx nimmt den
 *    Auftrag mit in die Ablage (`steuer_auftraege`). Abgearbeitet wird er von
 *    `steuerOutboxLauf.ts` — beim Start, bei `online`, beim Entsperren und
 *    nach jedem erfolgreichen Senden, mit Rückzug und begrenzten Versuchen.
 *
 * **Endgültig abgelehnt** (400, 403, 404, 410, 422) wird nicht eingereiht,
 * sondern geworfen (`SteuerAbgewiesenError`). Der Aufrufer nimmt seine lokale
 * Wirkung dann sofort zurück und sagt es. Scheitert ein eingereihter Auftrag
 * später endgültig, nimmt `nimmWirkungZurueck` sie zurück — aber nur, solange
 * der lokale Stand noch der ist, den die Wirkung gesetzt hat.
 */

import { sendeSteuerpaket, type SteuerKontext } from './gespraech/steuerung'
import {
  legeSteuerAuftragAn,
  loadLocalMessages,
  loescheSteuerAuftrag,
  schreibeSteuerAuftrag,
  updateMessageInLocalStore,
} from './messengerLocalStore'
import { anheftung, setzeAnheftung } from './nachrichtAnheftung'
import { setzeVerfallsfrist, verfallStand } from './nachrichtVerfall'
import {
  istEndgueltigAbgelehnt,
  steuerRueckzugMs,
  SteuerAbgewiesenError,
  SteuerOutboxVollError,
  type SteuerAuftrag,
  type SteuerEintrag,
  type SteuerRuecknahme,
} from './steuerOutboxTypen'

export type SteuerAusgang = 'gesendet' | 'eingereiht'

/**
 * Heißt dieser Fehler „die Wirkung ist nicht angenommen"?
 *
 * Nur dann nimmt der Aufrufer seine lokale Wirkung zurück. Ein Netzfehler,
 * ein 429 oder ein 5xx heißt dagegen „wartet in der Outbox" — die Wirkung
 * bleibt stehen, und der Lauf bringt sie später hinaus.
 */
export function istAbweisung(fehler: unknown): boolean {
  return fehler instanceof SteuerAbgewiesenError || fehler instanceof SteuerOutboxVollError
}

/** Eine Kennung, die sich nie ändert: sie ist zugleich `client_uuid`. */
function neueKennung(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `steuer-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * Die Felder, die erst beim Bauen dazukommen.
 *
 * Sie stehen nicht in der Ablage: die Mailbox und die Kennung vergibt der
 * Aufruf, der Urheber ist das angemeldete Konto, und ein alter Beleg wäre nach
 * einem Schlüsselwechsel falsch. Der Lauf setzt sie frisch ein.
 */
const ABGELEITET = new Set(['mailbox', 'client_uuid', 'sig', 'von_konto', 'von_geraet'])

function rohfelder(felder: Record<string, unknown>): Record<string, unknown> {
  const raus: Record<string, unknown> = {}
  for (const [name, wert] of Object.entries(felder || {})) {
    if (ABGELEITET.has(name)) continue
    if (wert === undefined) continue
    raus[name] = wert
  }
  return raus
}

/**
 * Schickt eine Wirkung los — und reiht sie ein, wenn das gerade nicht geht.
 *
 * Gibt `'gesendet'` oder `'eingereiht'` zurück. Wirft `SteuerAbgewiesenError`
 * bei einer endgültigen Ablehnung und `SteuerOutboxVollError`, wenn die
 * Warteschlange voll ist; in beiden Fällen hat der Aufrufer seine lokale
 * Wirkung zurückzunehmen.
 */
export async function sendeWirkung(
  kontext: SteuerKontext,
  eintrag: SteuerEintrag,
): Promise<SteuerAusgang> {
  if (!kontext.mailbox || !kontext.eigeneId || !eintrag?.typ) {
    throw new SteuerAbgewiesenError(null)
  }
  const id = neueKennung()

  try {
    const kennung = await sendeSteuerpaket(kontext, eintrag.felder, id)
    // `null` heißt: für dieses Gespräch gibt es gerade keinen Weg (keine
    // Geräte, verschlossener Messenger). Das ist kein Urteil, sondern ein
    // Zustand — der Auftrag wartet und wird beim nächsten Anlass erneut
    // versucht.
    if (kennung) return 'gesendet'
  } catch (fehler) {
    if (istEndgueltigAbgelehnt(fehler)) {
      const status = Number((fehler as { status?: unknown }).status)
      throw new SteuerAbgewiesenError(Number.isInteger(status) ? status : null)
    }
  }

  const jetzt = Date.now()
  const auftrag: SteuerAuftrag = {
    id,
    mailbox: kontext.mailbox,
    art: kontext.gruppenKontext ? 'gruppe' : 'direkt',
    peerId: kontext.drKontext?.peerId ?? null,
    groupId: kontext.gruppenKontext?.groupId ?? null,
    mitglieder: [...(kontext.gruppenKontext?.mitglieder ?? [])],
    istEigentuemer: Boolean(kontext.gruppenKontext?.istEigentuemer),
    typ: eintrag.typ,
    ziel: eintrag.ziel ?? null,
    zielId: typeof eintrag.zielId === 'number' ? eintrag.zielId : null,
    felder: rohfelder(eintrag.felder),
    ruecknahme: eintrag.ruecknahme,
    angelegtAm: jetzt,
    // Der sofortige Versuch oben zählt als erster: es bleiben höchstens fünf
    // weitere, zusammen also sechs (Punkt „maximal 6" im Auftrag).
    versuche: 1,
    naechsterVersuch: jetzt + steuerRueckzugMs(1),
  }

  const angenommen = await legeSteuerAuftragAn(auftrag)
  if (!angenommen) throw new SteuerOutboxVollError()
  return 'eingereiht'
}

/** Ein fortgeschriebener Stand nach einem gescheiterten Lauf. */
export async function merkeSteuerFehlversuch(
  auftrag: SteuerAuftrag,
  jetzt: number,
): Promise<void> {
  const versuche = (Number.isInteger(auftrag.versuche) ? auftrag.versuche : 0) + 1
  await schreibeSteuerAuftrag({
    ...auftrag,
    versuche,
    naechsterVersuch: jetzt + steuerRueckzugMs(versuche),
  })
}

export async function vergissSteuerAuftrag(id: string): Promise<void> {
  await loescheSteuerAuftrag(id)
}

function gleicheReaktionen(
  a: Record<string, number[]> | undefined,
  b: Record<string, number[]> | undefined,
): boolean {
  const links = a ?? {}
  const rechts = b ?? {}
  const namen = Object.keys(links)
  if (namen.length !== Object.keys(rechts).length) return false
  for (const name of namen) {
    const x = [...(links[name] ?? [])].sort((m, n) => m - n)
    const y = [...(rechts[name] ?? [])].sort((m, n) => m - n)
    if (x.length !== y.length || x.some((wert, i) => wert !== y[i])) return false
  }
  return true
}

/**
 * Nimmt eine endgültig gescheiterte Wirkung lokal zurück.
 *
 * Die Rücknahme greift nur, solange der lokale Stand noch der ist, den diese
 * Wirkung gesetzt hat. Hat der Mensch inzwischen etwas anderes getan, gilt
 * seins — eine alte Warteschlange darf eine neue Entscheidung nicht
 * überschreiben. Eine Löschung trägt `keine`: sie ist nicht umkehrbar.
 */
export async function nimmWirkungZurueck(auftrag: SteuerAuftrag): Promise<void> {
  const ruecknahme: SteuerRuecknahme | undefined = auftrag.ruecknahme
  if (!ruecknahme || ruecknahme.art === 'keine') return

  if (ruecknahme.art === 'anheftung') {
    const jetzt = anheftung(ruecknahme.mailbox)
    if (jetzt.clientUuid !== ruecknahme.nachher.clientUuid) return
    setzeAnheftung(ruecknahme.mailbox, ruecknahme.vorher.clientUuid, ruecknahme.vorher.stand)
    return
  }

  if (ruecknahme.art === 'verfall') {
    const jetzt = verfallStand(ruecknahme.mailbox)
    if (jetzt.sekunden !== ruecknahme.nachher.sekunden) return
    setzeVerfallsfrist(ruecknahme.mailbox, ruecknahme.vorher.sekunden, ruecknahme.vorher.stand)
    return
  }

  const zeilen = await loadLocalMessages(ruecknahme.mailbox)
  const zeile = zeilen.find(
    (m) =>
      (ruecknahme.ziel !== null && m.clientUuid === ruecknahme.ziel) ||
      (typeof ruecknahme.zielId === 'number' && m.id === ruecknahme.zielId),
  )
  if (!zeile) return
  const schluessel = zeile.clientUuid ?? zeile.id

  if (ruecknahme.art === 'reaktion') {
    if (!gleicheReaktionen(zeile.reaktionen, ruecknahme.nachher)) return
    await updateMessageInLocalStore(ruecknahme.mailbox, schluessel, {
      reaktionen: ruecknahme.vorher,
    })
    return
  }

  // `bearbeitung`: nur zurücknehmen, solange der Text noch der bearbeitete ist.
  if (zeile.text !== ruecknahme.nachher.text) return
  await updateMessageInLocalStore(ruecknahme.mailbox, schluessel, ruecknahme.vorher)
}
