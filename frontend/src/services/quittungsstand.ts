/**
 * Bis wohin für einen Chat schon quittiert wurde.
 *
 * ## Warum das überhaupt gemerkt wird
 *
 * Die Mailbox gibt bei jedem Abruf dieselben hundert Umschläge heraus. Die
 * höchste fremde Umschlagkennung darin ändert sich nicht, solange niemand
 * schreibt — eine Quittung darüber ist beim zweiten Mal also nur noch Lärm.
 *
 * Gemerkt wurde das bisher in zwei `useRef` im Messenger, und die beginnen bei
 * jedem Chatwechsel wieder bei null. Wer zwischen zwei Gesprächen hin- und
 * herklickt, schickt für dieselbe letzte Nachricht jedes Mal eine neue
 * Zustellquittung. Die landet im selben Hundert-Umschläge-Fenster wie die
 * Nachrichten und verdrängt sie: gemessen waren 64 % aller Umschläge in der
 * Mailbox Zustellquittungen, und was hinten herausfällt, ist unwiederbringlich
 * weg — eine Bearbeitung, eine Reaktion, eine Anheftung.
 *
 * ## Warum ein verlorener Eintrag nichts kostet
 *
 * Beide Quittungen sind kumulativ: sie nennen eine Obergrenze, keine einzelne
 * Nachricht. Geht eine verloren, deckt die nächste denselben Bereich mit ab,
 * sobald eine neue Nachricht eintrifft. Deshalb darf hier großzügig gemerkt
 * werden — auch dann, wenn der Versand hinterher scheitert. Andersherum wäre
 * es teuer: ein Stand, der erst nach erfolgreichem Versand steigt, schickt bei
 * jedem gescheiterten Versuch erneut, und genau das hat die Mailbox geflutet,
 * sobald der Server mit 429 bremste.
 *
 * Der Schlüssel trägt bewusst einen Doppelpunkt: alles mit dem Präfix
 * `msm_chat_` räumt der Messenger beim Öffnen weg.
 */

const SCHLUESSEL = 'msm:chat_quittungsstand'
/**
 * Derselbe Stand für die **Gegenseite** — bis wohin der Partner gelesen und
 * zugestellt hat.
 *
 * Ein eigener Schlüssel, weil es eine andere Aussage über andere Kennungen
 * ist: hier stehen die Umschlagkennungen **meiner** Nachrichten, dort die der
 * fremden. Ein gemeinsamer Eintrag würde beim Lesen die eine Zahl für die
 * andere halten.
 */
const SCHLUESSEL_PARTNER = 'msm:chat_partnerstand'

/** Bis zu welcher fremden Umschlagkennung schon quittiert wurde. */
export interface Quittungsstand {
  /** Höchste Kennung, für die eine Zustellquittung raus ist. */
  zugestellt: number
  /** Höchste Kennung, für die eine Lesequittung raus ist. */
  gelesen: number
}

const LEER: Quittungsstand = { zugestellt: 0, gelesen: 0 }

function lies(schluessel: string = SCHLUESSEL): Record<string, Quittungsstand> {
  try {
    const roh = localStorage.getItem(schluessel)
    const gelesen = roh ? JSON.parse(roh) : {}
    return gelesen && typeof gelesen === 'object' ? gelesen : {}
  } catch {
    return {}
  }
}

function standAus(schluessel: string, blindMailboxId: string): Quittungsstand {
  if (!blindMailboxId) return { ...LEER }
  const eintrag = lies(schluessel)[blindMailboxId]
  if (!eintrag || typeof eintrag !== 'object') return { ...LEER }
  return { zugestellt: zahl(eintrag.zugestellt), gelesen: zahl(eintrag.gelesen) }
}

function merke(
  schluessel: string,
  blindMailboxId: string,
  art: keyof Quittungsstand,
  bisId: number,
): void {
  if (!blindMailboxId || zahl(bisId) === 0) return
  const alle = lies(schluessel)
  const bisher = alle[blindMailboxId]
  const stand: Quittungsstand = {
    zugestellt: zahl(bisher?.zugestellt),
    gelesen: zahl(bisher?.gelesen),
  }
  if (bisId <= stand[art]) return
  stand[art] = bisId
  alle[blindMailboxId] = stand
  try {
    localStorage.setItem(schluessel, JSON.stringify(alle))
  } catch {
    // Ohne localStorage gilt der Stand eben nur für diese Sitzung.
  }
}

/**
 * Eine Umschlagkennung oder null.
 *
 * `Number.isFinite` reicht hier nicht: `1e308` ist endlich, und ein einziger
 * solcher Wert in der Ablage riegelt die Mailbox dauerhaft ab — jede echte
 * Kennung liegt darunter, also ginge nie wieder eine Quittung raus. Gefunden
 * am 21.09.2026 beim Beschiessen der Ablage mit Müll. Eine Kennung kommt aus
 * einer Datenbankspalte, ist also eine ganze Zahl in sicherer Reichweite; alles
 * andere ist kaputt und zählt als „nichts gemerkt".
 */
function zahl(wert: unknown): number {
  const n = Number(wert)
  return Number.isSafeInteger(n) && n > 0 ? n : 0
}

export function quittungsstand(blindMailboxId: string): Quittungsstand {
  return standAus(SCHLUESSEL, blindMailboxId)
}

/**
 * Den Stand hochsetzen.
 *
 * Nur nach oben: ein später eintreffender Abruf mit einem kleineren Höchstwert
 * darf den Stand nicht zurückdrehen, sonst quittiert das Gerät dieselbe
 * Nachricht ein zweites Mal.
 */
export function merkeQuittung(
  blindMailboxId: string,
  art: keyof Quittungsstand,
  bisId: number,
): void {
  merke(SCHLUESSEL, blindMailboxId, art, bisId)
}

/**
 * Bis wohin der **Gesprächspartner** gelesen und zugestellt hat.
 *
 * Bis 22.10.2026 stand dieser Stand nur in zwei `useRef` im Messenger und wurde
 * bei jedem Chatwechsel auf 0 gesetzt. Aufgefüllt wurde er aus den
 * Quittungsumschlägen der Mailbox — und weil jeder Abruf die **ganze** Mailbox
 * las, fand er sie dort jedes Mal wieder. Mit dem Cursor (Punkt 180) liegen
 * sie nach dem ersten Lesen hinter dem Stand, und die Häkchen fielen auf
 * „gesendet" zurück, obwohl der Partner längst gelesen hatte.
 *
 * Derselbe Merker wie für die eigene Seite: kumulativ, nur nach oben. Ein
 * Gerät, das seinen Stand verliert, holt ihn beim nächsten Lesen nach; ein
 * neuer Stand fällt nie unter einen alten.
 */
export function partnerstand(blindMailboxId: string): Quittungsstand {
  return standAus(SCHLUESSEL_PARTNER, blindMailboxId)
}

/** Was der Partner gelesen bzw. zugestellt hat, festhalten. */
export function merkePartnerQuittung(
  blindMailboxId: string,
  art: keyof Quittungsstand,
  bisId: number,
): void {
  merke(SCHLUESSEL_PARTNER, blindMailboxId, art, bisId)
}
