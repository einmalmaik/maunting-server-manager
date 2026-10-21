/** Umschlagformat, Typen und Fehler des Double Ratchet. */

import i18n from '@/i18n'
import { utf8ToBytes } from '@msdis/shield/core'

export const DR_PREFIX = 'sv-e2ee-dr-v1:'
export const DR_INIT_TYP = 'dr-init'

export const GEHEIMNIS_BYTES = 32
/** Reicht für die `client_uuid`: 36 (UUID) + 1 + 12 = 49 von 64 erlaubten Zeichen. */
export const KENNUNG_KURZ = 12

export interface DrKontext {
  /** Das eigene Konto. */
  eigeneId: number
  /** Das Konto der Gegenstelle in diesem Gespräch. */
  peerId: number
}

/**
 * Für kein einziges Zielgerät liess sich ein Umschlag bauen.
 *
 * Abzugrenzen von `E2eeKeinGeraetError`: dort ist niemand angemeldet, hier sind
 * Geräte da und das Verschlüsseln hat versagt. Der Unterschied gehört bis in
 * den Fehlertext, sonst sucht der Benutzer den Fehler bei der Gegenstelle.
 */
export class DrZustellungFehlgeschlagenError extends Error {
  constructor(public readonly ursachen: unknown[]) {
    super('Für kein Zielgerät liess sich ein Umschlag bauen')
    this.name = 'DrZustellungFehlgeschlagenError'
  }
}

/**
 * Dieses Gerät steht nicht mehr im Verzeichnis des eigenen Kontos.
 *
 * Jemand hat es in der Geräteliste entfernt, während dieser Tab offen war.
 * Senden hiesse Aufbauten, die drüben niemand prüfen kann; sich still neu
 * einzutragen hiesse, dem Entfernen zu nehmen, was der Dialog dort verspricht.
 * Der Text sagt deshalb beides: warum nicht, und dass ein Neuladen es wieder
 * einträgt — so, wie es der Dialog angekündigt hat.
 */
export class DrGeraetNichtEingetragenError extends Error {
  constructor() {
    super(i18n.t('messenger.deviceNotListed'))
    this.name = 'DrGeraetNichtEingetragenError'
  }
}

/** Ein Umschlagpaar für genau ein Zielgerät. Reihenfolge beachten. */
export interface DrZustellung {
  empfaengerId: number
  zielGeraet: string
  /** Muss **vor** `nachricht` zugestellt werden, sonst findet die Gegenstelle keine Sitzung. */
  bootstrap: string | null
  nachricht: string
  clientUuid: string
  bootstrapClientUuid: string
}

export type DrLesung =
  | { art: 'klartext'; text: string; vonKonto: number; vonGeraet: string }
  /** Kopie für ein anderes Gerät. Nie anzeigen, auch nicht als „verschlüsselt". */
  | { art: 'fremd' }
  /** Von diesem Gerät gesendet. Der Absender kann sich selbst nicht lesen — so ist der Ratchet gebaut. */
  | { art: 'eigen' }
  /** Kein Umschlag dieses Verfahrens. */
  | { art: 'unbekannt' }
  /**
   * Die Sitzung trägt nicht mehr: die Gegenstelle hat neu installiert, oder
   * jemand hat am Umschlag gedreht. Die Sitzung gehört weg und der Mensch soll
   * es sehen.
   */
  | { art: 'bruch'; vonKonto: number; vonGeraet: string; grund: string }
  /**
   * Dieser Umschlag ist schon einmal als Bruch gewertet worden.
   *
   * Anzeigen als „nicht lesbar", aber nichts mehr daraus folgern. Er bleibt bis
   * zum Ende seiner Aufbewahrung in der Mailbox liegen, und der Lesepfad holt
   * bei jedem Abruf das ganze Fenster — ohne diese Unterscheidung warf ein
   * einziger alter Umschlag bei jedem Öffnen des Gesprächs die gerade
   * funktionierende Sitzung weg.
   */
  | { art: 'beurteilt' }
  /**
   * Etwas anderes ging schief — meist die lokale Ablage. **Kein** Grund, eine
   * Sitzung wegzuwerfen: eine volle Platte darf nicht jeden Gesprächsfaden des
   * Geräts abreißen. Der Umschlag bleibt liegen und wird später erneut versucht.
   */
  | { art: 'fehler'; grund: string }
  /**
   * Noch nicht angefasst, weil der Sitzungsaufbau dieses Geräts im selben
   * Durchlauf nicht entschieden werden konnte. Nicht anzeigen; der nächste
   * Durchlauf versucht es erneut. Siehe `DrLeseOptionen.zurueckstellen`.
   */
  | { art: 'zurueckgestellt' }
  /**
   * Gehört zu einem abgewiesenen Sitzungsaufbau: das Gerät hat einen im
   * Fenster, und diese Nachricht öffnet sich gegen die bestehende Sitzung
   * nicht. Nicht anzeigen, und — der Grund für diese Art — **keine** Sitzung
   * verwerfen. Siehe `DrLeseOptionen.schonen`.
   */
  | { art: 'abgewiesen' }

/** Was `verarbeiteBootstrap` über einen Hybrid-Klartext herausgefunden hat. */
export interface AufbauErgebnis {
  /** Es war ein Sitzungsaufbau, gleich was daraus wurde. Gehört nie in den Verlauf. */
  istAufbau: boolean
  /** Eine bestehende Sitzung wurde ersetzt. Der Aufrufer macht das sichtbar. */
  ersetzt: boolean
  vonKonto?: number
  vonGeraet?: string
  /**
   * Noch nicht entschieden: das Verzeichnis antwortete nicht, oder es führt
   * das Gerät noch nicht. Nichts angewandt, nichts gemerkt — der nächste
   * Durchlauf versucht es erneut.
   *
   * Nachrichten dieses Geräts, die im selben Durchlauf folgen, gehören
   * zurückgestellt. Liefen sie gegen eine Sitzung, die es noch nicht gibt,
   * wären sie als Bruch gewertet und damit für immer verloren.
   */
  offen?: boolean
  /**
   * Abgewiesen und gemerkt: die Unterschrift passt nicht zum Verzeichnis, sie
   * fehlt bei einem Konto, das unterschreiben kann, oder der Aufbau nennt ein
   * Konto, das nicht zu diesem Gespräch gehört. Angewandt wurde nichts; der
   * Aufrufer macht es sichtbar.
   *
   * Nur beim ersten Sehen — gemeldet wird einmal.
   */
  abgelehnt?: boolean
  /**
   * Ein abgewiesener Aufbau, beim ersten Sehen und bei jedem weiteren. Solange
   * er im Fenster liegt, gehören unlesbare Nachrichten seines Geräts zu ihm und
   * nicht zur bestehenden Sitzung: `DrLeseOptionen.schonen`.
   */
  abgewiesen?: boolean
}

export interface DrLeseOptionen {
  /**
   * Liefert `true` für ein Gerät, dessen Sitzungsaufbau in diesem Durchlauf
   * offen geblieben ist (`AufbauErgebnis.offen`). Seine Nachrichten werden
   * dann nicht geöffnet, sondern als `zurueckgestellt` gemeldet.
   */
  zurueckstellen?: (vonKonto: number, vonGeraet: string) => boolean
  /**
   * Liefert `true` für ein Gerät, von dem ein abgewiesener Aufbau im Fenster
   * liegt (`AufbauErgebnis.abgewiesen`). Öffnet sich eine seiner Nachrichten
   * gegen die bestehende Sitzung nicht, ist das kein Bruch, sondern die
   * Nachricht hinter der Fälschung: `abgewiesen`, und die Sitzung bleibt.
   * Ohne das kippte ein gefälschter Aufbau mit einer einzigen Nachricht
   * dahinter die laufende Sitzung doch noch.
   */
  schonen?: (vonKonto: number, vonGeraet: string) => boolean
}

// ==========================================
// Format
// ==========================================

/**
 * Die gebundenen Daten einer Sitzung.
 *
 * Beide Gerätekennungen sortiert, damit Absender und Empfänger dieselbe
 * Zeichenkette bilden — die Sitzung trägt sie ab dem Aufbau unverändert, und
 * beide Richtungen laufen durch dieselbe Sitzung.
 */
export function gebundeneDaten(kontext: DrKontext, geraetA: string, geraetB: string): Uint8Array {
  const kleiner = Math.min(kontext.eigeneId, kontext.peerId)
  const groesser = Math.max(kontext.eigeneId, kontext.peerId)
  const [erst, zweit] = [geraetA, geraetB].sort()
  return utf8ToBytes(`msm:dr:${kleiner}:${groesser}:${erst}:${zweit}`)
}

export interface DrKopf {
  vonKonto: number
  vonGeraet: string
  fuerGeraet: string
  rumpf: string
}

export function baueUmschlag(kopf: DrKopf): string {
  return `${DR_PREFIX}${kopf.vonKonto}.${kopf.vonGeraet}.${kopf.fuerGeraet}.${kopf.rumpf}`
}

export function lieseKopf(umschlag: string): DrKopf | null {
  if (!umschlag.startsWith(DR_PREFIX)) return null
  const teile = umschlag.slice(DR_PREFIX.length).split('.')
  if (teile.length !== 4) return null
  const [kontoRoh, vonGeraet, fuerGeraet, rumpf] = teile
  const vonKonto = Number(kontoRoh)
  if (!Number.isInteger(vonKonto) || vonKonto <= 0) return null
  if (!vonGeraet || !fuerGeraet || !rumpf) return null
  return { vonKonto, vonGeraet, fuerGeraet, rumpf }
}

/**
 * Konto und Gerät aus dem Kopf eines Ratchet-Umschlags, oder `null`.
 *
 * Der Kopf wählt die Sitzung, gegen die entschlüsselt wird — was sich damit
 * öffnen liess, kam von diesem Gerät. Für Klartext aus der Ablage ist das die
 * einzige Quelle: der Zwischenspeicher, der sich den Absender beim Öffnen
 * gemerkt hat, lebt nur bis zum Neuladen. Ohne Absender griffe die
 * Downgrade-Schranke im Messenger nicht, und eine Nachricht, die sie beim
 * ersten Sehen verworfen hat, stünde nach dem Neuladen im Verlauf.
 *
 * Nur für Umschläge, die dieses Gerät schon geöffnet hat. Einem fremden Kopf
 * glaubt hier niemand etwas.
 */
export function drUrheber(umschlag: string): { vonKonto: number; vonGeraet: string } | null {
  const kopf = lieseKopf(umschlag)
  return kopf ? { vonKonto: kopf.vonKonto, vonGeraet: kopf.vonGeraet } : null
}

/**
 * Schneidet den Gerätezusatz von der Kennung ab.
 *
 * Dieselbe Nachricht liegt je Zielgerät einmal in der Mailbox. Damit alle Geräte
 * denselben Verlauf führen, zählt für die Anzeige und den lokalen Speicher die
 * Kennung ohne Zusatz.
 */
export function logischeUuid(clientUuid: string | null | undefined): string | undefined {
  if (!clientUuid) return undefined
  const schnitt = clientUuid.indexOf('#')
  return schnitt === -1 ? clientUuid : clientUuid.slice(0, schnitt)
}

// ==========================================
// Sitzungsaufbau
// ==========================================

export interface BootstrapInhalt {
  typ: string
  v: number
  vonKonto: number
  vonGeraet: string
  fuerGeraet: string
  geheimnis: string
  paar: { algorithm: string; publicKey: string; privateKey: string }
  /**
   * Die Unterschrift des sendenden Geräts über `aufbauDaten`. Fehlt sie, gilt
   * die Downgrade-Schranke aus `pruefeGeraeteBeleg`: durch kommt dann nur, wer
   * gar nicht unterschreiben *kann*.
   */
  sig?: string
}

/**
 * Hat der Klartext die Form eines Aufbaus?
 *
 * Nicht bloß Hygiene: ein Aufbau mit fehlendem Konto oder Gerät liefe sonst
 * bis in die Verzeichnisabfrage, bliebe dort `offen` und kostete bei jedem
 * Abruf eine Anfrage, die nie beantwortet werden kann.
 */
export function istAufbauInhalt(roh: any): roh is BootstrapInhalt {
  return (
    Number.isInteger(roh.vonKonto) &&
    roh.vonKonto > 0 &&
    typeof roh.vonGeraet === 'string' &&
    roh.vonGeraet.length > 0 &&
    typeof roh.fuerGeraet === 'string' &&
    typeof roh.geheimnis === 'string' &&
    Boolean(roh.paar) &&
    typeof roh.paar.algorithm === 'string' &&
    typeof roh.paar.publicKey === 'string' &&
    typeof roh.paar.privateKey === 'string'
  )
}

/**
 * Die Zeichenkette, über die ein Sitzungsaufbau unterschrieben wird.
 *
 * Alles, was die Sitzung ausmacht: von wem, für wen, in welchem Gespräch, und
 * das Material selbst. Das Gespräch steht als Kontenpaar darin und nicht als
 * Mailbox — die Mailbox zieht mit dem Chatgeheimnis um, das Gespräch nicht.
 *
 * Ein JSON-Array statt einer Feldliste: Reihenfolge und Grenzen sind damit
 * eindeutig, ohne dass jemand ein Trennzeichen maskieren muss. Der Vorsatz
 * hält diese Unterschrift von denen über Nutzlasten (`msm:nutzlast:v1:`)
 * fern, die mit demselben Schlüssel entstehen.
 */
export function aufbauDaten(kontext: DrKontext, inhalt: BootstrapInhalt): string {
  return JSON.stringify([
    'msm:dr-init:v1',
    Math.min(kontext.eigeneId, kontext.peerId),
    Math.max(kontext.eigeneId, kontext.peerId),
    inhalt.vonKonto,
    inhalt.vonGeraet,
    inhalt.fuerGeraet,
    inhalt.geheimnis,
    inhalt.paar.algorithm,
    inhalt.paar.publicKey,
    inhalt.paar.privateKey,
  ])
}
