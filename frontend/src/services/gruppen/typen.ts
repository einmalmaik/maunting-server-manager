/**
 * Typen und reine Helfer des Gruppenschluessels.
 *
 * Keine Ablage, kein Netz: alles hier laesst sich ohne Nebenwirkung lesen.
 */

import { base64ToBytes } from '@msdis/shield/core'
import { importAesGcmRawKey } from '@msdis/shield/aead'

export const GRUPPE_PREFIX = 'sv-e2ee-group-v1:'
export const GRUPPEN_SCHLUESSEL_TYP = 'group_key'
export const GRUPPEN_ANFRAGE_TYP = 'group_key_request'

/**
 * Der Gruppenschlüssel hat kein einziges Gerät erreicht.
 *
 * Nicht zu verwechseln mit einer unvollständigen Zustellung: die ist eingeplant,
 * die übergangenen Geräte fordern nach. Erreicht aber **niemand** den Schlüssel,
 * wäre jede folgende Nachricht für alle ausser dem Absender unlesbar.
 */
export class GruppenSchluesselNichtZugestelltError extends Error {
  constructor(
    public readonly groupId: number,
    public readonly ziele: number,
  ) {
    super('Der Gruppenschlüssel erreichte kein Gerät')
    this.name = 'GruppenSchluesselNichtZugestelltError'
  }
}

export const SCHLUESSEL_BYTES = 32
export const KEY_ID_LAENGE = 16
export const KEY_ID_MUSTER = /^[0-9a-f]{16}$/

export interface GruppenKontext {
  groupId: number
  /** Die Mailbox der Gruppe. Dort liegen Nachrichten und Schlüsselumschläge. */
  blindMailboxId: string
  eigeneId: number
  /** Die Mitglieder, wie dieses Gerät sie gerade sieht. Quelle jeder Rotation. */
  mitglieder: readonly number[]
  /**
   * Gehört die Gruppe mir?
   *
   * Pflichtfeld, kein optionales: gebraucht wird es nur an einer Stelle — wer
   * das Gruppengeheimnis einer Bestandsgruppe erzeugen darf —, und dort
   * entscheidet es darüber, ob die Gruppe in zwei Mailboxen zerfällt. Ein
   * vergessenes `true` wäre ein Fehler, den niemand sieht; ein vergessenes
   * Feld meldet der Typprüfer.
   */
  istEigentuemer: boolean
}

export type GruppenLesung =
  /**
   * Der Klartext — und keine Aussage darüber, wer ihn geschrieben hat. Ein
   * Gruppenschlüssel beweist Mitgliedschaft, nie Identität; wer den Absender
   * braucht, prüft die Nutzlastsignatur (`nutzlastSignatur.ts`).
   */
  | { art: 'klartext'; text: string }
  /** Kein Schlüssel zu dieser Kennung. Der Aufrufer darf nachfordern. */
  | { art: 'kein-schluessel'; keyId: string }
  /** Kein Umschlag dieses Verfahrens — Altbestand aus der Zeit der Ableitung. */
  | { art: 'unbekannt' }
  /** Schlüssel vorhanden, Tag gescheitert: am Umschlag wurde gedreht. */
  | { art: 'bruch'; keyId: string; grund: string }

export type GruppenSteuerung =
  | { art: 'schluessel'; keyId: string }
  | { art: 'anfrage'; vonKonto: number; beantwortet: boolean }
  | { art: 'keine' }


export interface GruppenSchluesselEintrag {
  groupId: number
  keyId: string
  /** Base64 der 32 Schlüsselbytes. */
  schluessel: string
  /** Die Mitgliedschaft, für die dieser Schlüssel gemünzt wurde. Sortiert. */
  mitglieder: number[]
  erzeugtAm: string
}

/**
 * Das Gruppengeheimnis — 32 Bytes, die eine Gruppe ihr Leben lang behält.
 *
 * Nicht zu verwechseln mit dem Nachrichtenschlüssel darüber: **der** rotiert
 * bei jedem Mitgliederwechsel, und das muss er auch. Das Geheimnis darf das
 * nicht, denn aus ihm entsteht die Kennung der Mailbox. Rotierte es mit, zöge
 * die Gruppe bei jedem Beitritt um und liesse ihr Fenster stehen.
 *
 * Es reist mit dem Schlüssel: dieselbe Zustellung, dieselben Empfänger. Ein
 * eigener Weg wäre ein zweiter, der genauso oft scheitern kann — und wer den
 * Schlüssel nicht hat, kann mit dem Geheimnis ohnehin nichts anfangen.
 */
export interface GruppenGeheimnis {
  groupId: number
  /** Base64 der 32 Geheimnisbytes. */
  geheimnis: string
  erzeugtAm: string
}

/**
 * Dasselbe für einen Direktchat — das Gegenstück zu `GruppenGeheimnis`.
 *
 * Warum überhaupt eines, wo der Double Ratchet doch ein gemeinsames Geheimnis
 * hat: **er hat eines je Gerätepaar, nicht je Gespräch.** Zwei Leute mit je
 * zwei Geräten haben vier Ratchets mit vier verschiedenen Wurzeln. Fiele die
 * Mailbox daraus, hätte dasselbe Gespräch vier Mailboxen, und jedes Gerät
 * sähe nur ein Viertel davon.
 *
 * Also dieselbe Bauform wie bei der Gruppe: ein Zufallswert, den alle Geräte
 * beider Seiten teilen. Der Ratchet bleibt, was er ist — er verschlüsselt die
 * Nachrichten. Das Geheimnis hier benennt nur den Ort.
 */
export interface DmGeheimnis {
  /** Das Konto der Gegenstelle. Die eigene Seite steckt in der Datenbankwahl. */
  peerId: number
  /** Base64 der 32 Geheimnisbytes. */
  geheimnis: string
  erzeugtAm: string
  /**
   * Wann die Gegenstelle es bekam — und bis dahin wird **nicht** umgezogen.
   *
   * Der Fall, an dem das hängt: die allererste Nachricht an jemanden, mit dem
   * es noch keine Chatzeile gibt. Der Server lässt einen Steuerumschlag in
   * eine fremde Geräte-Mailbox nur bei bestehender Beziehung durch; die
   * entsteht aber erst mit dieser Nachricht. Zöge das Gespräch trotzdem um,
   * ginge die Nachricht in eine Mailbox, die die Gegenseite nicht kennt und
   * nie erfährt — und die Zustellung, die ihr das sagen würde, käme nie
   * zustande. Ein Ring, der sich nicht von selbst öffnet.
   *
   * Darum: erzeugt wird sofort, umgezogen erst, wenn wirklich ein Gerät der
   * **Gegenstelle** erreicht wurde. Vorher bleibt es bei der abgeleiteten
   * Kennung, und jeder Versand versucht die Zustellung erneut.
   *
   * Beim Empfänger steht hier der Augenblick des Empfangs: er hat es ja.
   */
  verteiltAm?: string | null
  /**
   * Welche **eigenen** Geräte es bekommen haben.
   *
   * Ohne diese Liste wusste der Absender nur, *dass* er die eigenen Geräte
   * einmal bedient hatte — nicht welche. Ein später gekoppeltes Gerät fiel
   * damit durch jedes Raster, und der Verlauf lief auseinander (bis 10/2026).
   * Was hier fehlt, wird beim nächsten Versand nachgetragen.
   */
  verteiltAn?: string[]
  /**
   * Der Umschlag, mit dem das Geheimnis kam — unverändert, mit der
   * Unterschrift dessen, der es erzeugt hat.
   *
   * **Erzeugen darf nur die kleinere Kennung** (`darfDmGeheimnisErzeugen`),
   * und das prüft auch der Empfänger. Ein später gekoppeltes Gerät auf der
   * **grösseren** Seite bekam den Umschlag deshalb von seinem Geschwistergerät
   * — und der trug die grössere Kennung, also wies das neue Gerät ihn ab. Der
   * Absender vermerkte es trotzdem in `verteiltAn`, und kein späterer Versand
   * versuchte es je wieder (bis 20.10.2026).
   *
   * Darum reist das Geheimnis in diesem Fall mit dem **Original** weiter: die
   * Unterschrift der Gegenseite gilt unverändert, und die Regel bleibt, wie
   * sie ist. Nur dieses Gerät kann den Umschlag weitergeben — es hat ihn
   * selbst geprüft bekommen.
   */
  beleg?: Record<string, unknown>
}

export interface GruppenAblage {
  lies(groupId: number, keyId: string): Promise<GruppenSchluesselEintrag | null>
  liesAktuellen(groupId: number): Promise<GruppenSchluesselEintrag | null>
  /** Das Geheimnis dieser Gruppe, falls dieses Gerät es kennt. */
  liesGeheimnis(groupId: number): Promise<GruppenGeheimnis | null>
  /** Das Geheimnis dieses Direktchats, falls dieses Gerät es kennt. */
  liesDmGeheimnis(peerId: number): Promise<DmGeheimnis | null>
  /** Legt es ab und überschreibt ein vorhandenes **nie** — wie bei der Gruppe. */
  schreibeDmGeheimnis(eintrag: DmGeheimnis): Promise<void>
  /**
   * Trägt nach, dass die Gegenstelle es hat — und welche eigenen Geräte.
   *
   * Der einzige erlaubte Nachtrag: das Geheimnis selbst bleibt unberührt, und
   * `verteiltAm` wird nie zurückgenommen. Die Liste der eigenen Geräte wächst
   * nur, damit ein später dazugekommenes Gerät beim nächsten Versand drankommt.
   */
  merkeDmVerteilung(
    peerId: number,
    wann: string,
    eigeneGeraete?: readonly string[],
  ): Promise<void>
  /**
   * Legt das Geheimnis ab — und überschreibt ein vorhandenes **nie**.
   *
   * Zwei Geräte, die gleichzeitig eines erzeugen, wären zwei Mailboxen und
   * damit eine gespaltene Gruppe. Wer schon eines hat, behält es; der andere
   * erfährt seines mit der nächsten Schlüsselzustellung.
   */
  schreibeGeheimnis(eintrag: GruppenGeheimnis): Promise<void>
  /** Legt den Schlüssel ab und macht ihn zum aktuellen dieser Gruppe. */
  schreibe(eintrag: GruppenSchluesselEintrag): Promise<void>
  /**
   * Legt den Schlüssel ab, **ohne** den aktuellen zu ersetzen.
   *
   * Der Weg für einen zugestellten Schlüssel, dessen Generation dieses Gerät
   * schon bedient: lesen soll er, senden nicht. Warum, steht bei `nimmSchluessel`.
   */
  schreibeNebenher(eintrag: GruppenSchluesselEintrag): Promise<void>
  loescheGruppe(groupId: number): Promise<void>
  /** Wurde diese Nachfrage schon beantwortet? Siehe Kopf der Datei. */
  kennstAnfrage(groupId: number, kennung: string): Promise<boolean>
  /** Erst rufen, wenn die Antwort wirklich rausging. */
  merkeAnfrage(groupId: number, kennung: string): Promise<void>
}


export function gebundeneDaten(groupId: number, keyId: string): string {
  return `msm:group:aad:${groupId}:${keyId}`
}

/**
 * Führt eine Operation mit dem entpackten Schlüssel aus und nullt die Bytes
 * danach. Der CryptoKey selbst ist nicht auslesbar; die Rohbytes wären es.
 */
export async function mitSchluessel<T>(
  eintrag: GruppenSchluesselEintrag,
  arbeit: (key: CryptoKey) => Promise<T>,
): Promise<T> {
  const bytes = base64ToBytes(eintrag.schluessel)
  try {
    return await arbeit(await importAesGcmRawKey(bytes, ['encrypt', 'decrypt']))
  } finally {
    bytes.fill(0)
  }
}

export function lieseGruppenKopf(umschlag: string): { keyId: string; rumpf: string } | null {
  if (!umschlag.startsWith(GRUPPE_PREFIX)) return null
  const nutzlast = umschlag.slice(GRUPPE_PREFIX.length)
  const punkt = nutzlast.indexOf('.')
  if (punkt === -1) return null
  const keyId = nutzlast.slice(0, punkt)
  const rumpf = nutzlast.slice(punkt + 1)
  if (!KEY_ID_MUSTER.test(keyId) || !rumpf) return null
  return { keyId, rumpf }
}

/** Sortiert, entdoppelt, ohne Unsinn — damit zwei Geräte denselben Vergleich bilden. */
export function normalisiereMitglieder(ids: readonly unknown[]): number[] {
  const sauber = new Set<number>()
  for (const roh of ids) {
    const id = Number(roh)
    if (Number.isInteger(id) && id > 0) sauber.add(id)
  }
  return [...sauber].sort((a, b) => a - b)
}

export function gleicheMitglieder(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i])
}
