/**
 * @Erwähnungen in Gruppen — und warum das Recht beim **Empfänger** greift.
 *
 * ## Das Problem
 *
 * Der Server sieht den Umschlag, nicht den Text. Er kann also nicht prüfen, ob
 * eine Nachricht `@everyone` enthält, und soll es auch nicht können. Ein Recht,
 * das nur der sendende Client abfragt, ist aber kein Recht: ein veränderter
 * Client schreibt es trotzdem hinein.
 *
 * ## Die Lösung
 *
 * Eine Erwähnung **wirkt** erst beim Empfänger — dort entsteht das Klingeln,
 * das Abzeichen, die Hervorhebung. Und der Empfänger hat beides: den Klartext
 * und, über `/social/groups`, die maßgebliche Rechtelage aller Mitglieder.
 *
 * > Bevor aus `@everyone` eine Erwähnung wird, prüft der empfangende Client, ob
 * > der **Absender** das Recht `mention_everyone` hatte. Hatte er es nicht,
 * > bleibt `@everyone` gewöhnlicher Text.
 *
 * ## Und die Regel wird nicht nachgebaut
 *
 * CLAUDE.md §4 verbietet es, und es wäre auch falsch: das Frontend kennt weder
 * die Rollenvorgaben noch die Überschreibungen je Mitglied vollständig. Der
 * Server rechnet die Regel aus und legt das Ergebnis **je Mitglied** in die
 * Gruppenantwort (`members[].can_mention_everyone`), genau wie er es für
 * `can_start_call` schon tut. Hier wird sie nur gelesen.
 *
 * ## Namen im Text, Kennungen in der Wirkung
 *
 * Der Text trägt `@name`, so wie er geschrieben wurde — ein Zitat ändert sich
 * nicht, wenn jemand sich umbenennt. Geweckt wird über `erwaehnungen: number[]`,
 * also über Konto-Kennungen. Beides hat seine Aufgabe, und keines vertritt das
 * andere.
 */

import type { ChatGroupItem, ChatGroupMemberItem } from '@/api/social'

/** Was statt eines Namens alle meint. */
export const ALLE_WORTE = ['everyone', 'here', 'alle'] as const

/** Ein Stück Text, wie es die Blase rendert. */
export interface Textstueck {
  art: 'text' | 'erwaehnung'
  inhalt: string
}

/**
 * Wonach im Text gesucht wird.
 *
 * Namen dürfen Punkt, Strich und Unterstrich enthalten, aber nicht auf einem
 * enden — sonst schluckt `@maik.` den Satzpunkt.
 */
const ERWAEHNUNG = /@([A-Za-z0-9_][A-Za-z0-9_.-]*[A-Za-z0-9_]|[A-Za-z0-9_])/g

export interface ErwaehnungsFund {
  /** Konto-Kennungen der genannten Mitglieder. */
  erwaehnungen: number[]
  /** Ob im Text `@everyone`, `@here` oder `@alle` steht. */
  erwaehntAlle: boolean
}

/**
 * Liest aus einem geschriebenen Text heraus, wer gemeint ist.
 *
 * Läuft beim **Senden**. Was hier nicht erkannt wird, ist gewöhnlicher Text und
 * weckt niemanden — ein Tippfehler im Namen ist keine Erwähnung.
 */
export function findeErwaehnungen(
  text: string,
  mitglieder: readonly ChatGroupMemberItem[],
): ErwaehnungsFund {
  const nachName = new Map<string, number>()
  for (const m of mitglieder) nachName.set(m.username.toLowerCase(), m.user_id)

  const gefunden = new Set<number>()
  let alle = false
  for (const treffer of text.matchAll(ERWAEHNUNG)) {
    const wort = treffer[1].toLowerCase()
    if ((ALLE_WORTE as readonly string[]).includes(wort)) {
      alle = true
      continue
    }
    const id = nachName.get(wort)
    if (id) gefunden.add(id)
  }
  return { erwaehnungen: [...gefunden], erwaehntAlle: alle }
}

/**
 * Ob der Absender alle wecken durfte.
 *
 * Die Antwort kommt aus der Gruppenantwort des Servers, nicht aus einer hier
 * nachgebauten Rollenlogik. Fehlt das Feld — alte Serverfassung, Mitglied nicht
 * in der Liste —, gilt **nein**: ein sicherer Ausgangszustand ist eine
 * ausbleibende Meldung, keine unberechtigte.
 */
export function darfAlleWecken(
  gruppe: Pick<ChatGroupItem, 'members'> | null | undefined,
  absenderId: number,
): boolean {
  if (!gruppe?.members) return false
  const m = gruppe.members.find((x) => Number(x.user_id) === Number(absenderId))
  return Boolean(m?.can_mention_everyone)
}

/**
 * Ob **ich** von dieser Nachricht gemeint bin.
 *
 * Die einzelne Erwähnung braucht kein Recht: wer schreiben darf, darf jemanden
 * ansprechen. Nur das Wecken aller ist beschränkt, und genau dort greift die
 * Prüfung gegen den Absender.
 */
export function binIchGemeint(
  msg: { senderId: number; erwaehnungen?: number[]; erwaehntAlle?: boolean; isSelf?: boolean },
  eigeneId: number,
  gruppe: Pick<ChatGroupItem, 'members'> | null | undefined,
): boolean {
  if (!eigeneId || msg.isSelf) return false
  if (msg.erwaehnungen?.some((id) => Number(id) === Number(eigeneId))) return true
  if (msg.erwaehntAlle && darfAlleWecken(gruppe, msg.senderId)) return true
  return false
}

/**
 * Welche `@worte` im Text hervorgehoben werden.
 *
 * Getrennt von `binIchGemeint`, weil die Hervorhebung jeden genannten Namen
 * betrifft, das Wecken aber nur mich.
 */
export function hervorzuhebendeWorte(
  msg: { senderId: number; erwaehnungen?: number[]; erwaehntAlle?: boolean },
  gruppe: Pick<ChatGroupItem, 'members'> | null | undefined,
): ReadonlySet<string> {
  const worte = new Set<string>()
  const mitglieder = gruppe?.members || []
  for (const id of msg.erwaehnungen || []) {
    const m = mitglieder.find((x) => Number(x.user_id) === Number(id))
    if (m) worte.add(m.username.toLowerCase())
  }
  if (msg.erwaehntAlle && darfAlleWecken(gruppe, msg.senderId)) {
    for (const w of ALLE_WORTE) worte.add(w)
  }
  return worte
}

/**
 * Zerlegt den Text in gewöhnliche Stücke und hervorzuhebende Erwähnungen.
 *
 * Ohne Treffer kommt ein einziges Stück zurück; die Blase rendert dann wie
 * bisher genau einen Textknoten.
 */
export function teileText(text: string, worte: ReadonlySet<string>): Textstueck[] {
  if (!text || worte.size === 0) return [{ art: 'text', inhalt: text }]

  const stuecke: Textstueck[] = []
  let zuletzt = 0
  for (const treffer of text.matchAll(ERWAEHNUNG)) {
    const start = treffer.index ?? 0
    if (!worte.has(treffer[1].toLowerCase())) continue
    if (start > zuletzt) stuecke.push({ art: 'text', inhalt: text.slice(zuletzt, start) })
    stuecke.push({ art: 'erwaehnung', inhalt: treffer[0] })
    zuletzt = start + treffer[0].length
  }
  if (zuletzt < text.length) stuecke.push({ art: 'text', inhalt: text.slice(zuletzt) })
  return stuecke.length ? stuecke : [{ art: 'text', inhalt: text }]
}

/** Ein Vorschlag in der Liste, die beim Tippen von `@` aufgeht. */
export interface Erwaehnungsvorschlag {
  /** Konto-Kennung, oder `null` für `@everyone`. */
  userId: number | null
  name: string
  avatarUrl?: string | null
  /** Nur für die Zeile „Alle benachrichtigen". */
  istAlle?: boolean
}

/** So viele Zeilen passen über der Tastatur, ohne die Eingabe zu verdecken. */
export const VORSCHLAEGE_MAX = 5

/**
 * Was die Vorschlagsliste zeigt, während jemand `@…` tippt.
 *
 * `@everyone` steht nur drin, wenn der Server es diesem Konto zugestanden hat.
 * Das ist Bequemlichkeit, keine Schranke — die Schranke sitzt beim Empfänger.
 */
export function sucheVorschlaege(
  gruppe: ChatGroupItem | null | undefined,
  praefix: string,
  eigeneId: number,
  hoechstens: number = VORSCHLAEGE_MAX,
): Erwaehnungsvorschlag[] {
  if (!gruppe) return []
  const suche = praefix.toLowerCase()
  const treffer: Erwaehnungsvorschlag[] = []

  if (gruppe.can_mention_everyone && ('everyone'.startsWith(suche) || 'alle'.startsWith(suche))) {
    treffer.push({ userId: null, name: 'everyone', istAlle: true })
  }
  for (const m of gruppe.members || []) {
    if (Number(m.user_id) === Number(eigeneId)) continue
    if (suche && !m.username.toLowerCase().startsWith(suche)) continue
    treffer.push({ userId: m.user_id, name: m.username, avatarUrl: m.avatar_url })
    if (treffer.length >= hoechstens) break
  }
  return treffer.slice(0, hoechstens)
}

/**
 * Was gerade getippt wird, wenn der Cursor hinter einem `@…` steht.
 *
 * `null` heißt „keine Erwähnung im Gange". Ein `@` mitten in einem Wort
 * (`post@example`) zählt nicht, sonst ginge die Liste bei jeder E-Mail-Adresse
 * auf.
 */
export function offeneErwaehnung(text: string, cursor: number): { praefix: string; start: number } | null {
  const bisCursor = text.slice(0, cursor)
  const at = bisCursor.lastIndexOf('@')
  if (at < 0) return null
  if (at > 0 && !/\s/.test(bisCursor[at - 1])) return null
  const praefix = bisCursor.slice(at + 1)
  if (/\s/.test(praefix)) return null
  return { praefix, start: at }
}

/** Setzt den gewählten Namen an die Stelle des getippten `@…`. */
export function setzeVorschlagEin(
  text: string,
  start: number,
  cursor: number,
  name: string,
): { text: string; cursor: number } {
  const neu = `${text.slice(0, start)}@${name} ${text.slice(cursor)}`
  return { text: neu, cursor: start + name.length + 2 }
}
