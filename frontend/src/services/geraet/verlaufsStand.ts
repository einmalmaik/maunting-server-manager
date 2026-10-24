/**
 * Der Fortschritt einer Verlaufsübergabe — auf der Platte, nicht im Speicher.
 *
 * Eine Übergabe kann Stunden dauern: der Messenger wird geschlossen, das Netz
 * fällt aus, ein Teil geht verloren. Ein Merker im Arbeitsspeicher wäre nach
 * dem nächsten Start weg, und die Quelle würde entweder alles doppelt schicken
 * oder — schlimmer — einen fehlenden Teil für erledigt halten. Deshalb liegen
 * beide Seiten hier in der kontogebundenen Ablage (`offlineAblage`): die
 * Quelle merkt sich ihren Plan und die bestätigten Teile, das Ziel den
 * Empfang je Transfer.
 *
 * **Kein Inhalt.** Gespeichert werden Kennungen, Teilnummern und Zeitpunkte.
 * Die Nachrichten selbst bleiben im Verlaufsspeicher und in den vorhandenen
 * Paketen. Die Ablage fällt beim Abmelden mit der ganzen kontogebundenen
 * Ablage weg; ein anderes Konto sieht sie nie.
 */

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { MESSENGER_KEYS, getStorageItem, setStorageItem } from '@/lib/offlineAblage'
import type { VerlaufsArt } from '../messengerLocalStore'

/** Ein Posten, wie ihn das Manifest nennt. */
export interface VerlaufsPosten {
  mailbox: string
  art: VerlaufsArt
  teile: number
  anzahl: number
}

/**
 * Ein Posten im Plan der Quelle. `grenzen` sind die kumulativen
 * Nachrichtenzahlen je Teil; damit findet ein zweiter Lauf dieselben Grenzen,
 * ohne die Nachrichten selbst abzulegen.
 */
export interface PlanPosten extends VerlaufsPosten {
  grenzen: number[]
}

/** Was die Quelle über eine laufende Übergabe an ein Zielgerät weiß. */
export interface QuellPlan {
  v: 1
  transferId: string
  ziel: string
  begonnenAm: number
  ablauf: number
  posten: PlanPosten[]
  /**
   * Je Mailbox ein Bitfeld über die Teile (`'0'`/`'1'`). Ein Feld statt einer
   * Zeile je Teil: 1000 Teile sind 1000 Zeichen, nicht 1000 localStorage-
   * Einträge, und die Ablage bleibt auch bei vielen Mailboxen klein.
   */
  quittiert: Record<string, string>
}

/** Was das Ziel über eine angenommene Übergabe weiß. */
export interface EmpfangsStand {
  v: 1
  transferId: string
  /** Dieses Gerät. */
  geraet: string
  /** Das Quellgerät, an das die Quittungen gehen. */
  von: string
  begonnenAm: number
  ablauf: number
  posten: VerlaufsPosten[]
  /** Je Mailbox ein Bitfeld der angenommenen Teile (`'0'`/`'1'`). */
  teile: Record<string, string>
}

/** So viele Übertragungen je Richtung werden höchstens behalten. */
const MAX_PLAENE = 8
const MAX_EMPFAENGE = 8

/** Schutz gegen ein Manifest, das den Speicher mit Posten flutet. */
export const MAX_POSTEN = 200
export const MAX_TEILE_GESAMT = 20_000

/** Der alte Absendermerker; bleibt derselbe Schlüssel wie vor 10/2026. */
const GESENDET_SCHLUESSEL = 'msm:history_sent'

export function gesendetAn(konto: number, geraet: string): boolean {
  try {
    return Boolean(window.localStorage.getItem(`${GESENDET_SCHLUESSEL}:${konto}:${geraet}`))
  } catch {
    return false
  }
}

export function merkeGesendet(konto: number, geraet: string): void {
  try {
    window.localStorage.setItem(`${GESENDET_SCHLUESSEL}:${konto}:${geraet}`, new Date().toISOString())
  } catch {
    // Ohne Ablage geht die Übergabe beim nächsten Start noch einmal; das
    // Zusammenführen kostet dann nur Zeit, keinen Inhalt.
  }
}

/**
 * Hält fest, dass ein Gerät seinen Verlauf vollständig bekommen hat.
 *
 * Der Merker fällt **erst nach der Empfangsquittung** (`history_done`), nicht
 * nach dem Absenden. Bis 10/2026 galt ein Verlauf als übergeben, sobald die
 * Umschläge beim Relais lagen; ein unterwegs verlorener Teil fiel damit
 * niemandem auf, und das neue Gerät behielt die Lücke für immer.
 */
export function merkeVerlaufGesendet(geraet: string): void {
  const konto = angemeldetesKonto()
  if (!konto || !geraet) return
  merkeGesendet(konto, geraet)
}

function liesKarte<T>(schluessel: string): Record<string, T> {
  const roh = getStorageItem(schluessel)
  if (!roh) return {}
  try {
    const geparst = JSON.parse(roh)
    return geparst && typeof geparst === 'object' && !Array.isArray(geparst)
      ? (geparst as Record<string, T>)
      : {}
  } catch {
    return {}
  }
}

function schreibeKarte<T>(schluessel: string, karte: Record<string, T>): void {
  try {
    setStorageItem(schluessel, JSON.stringify(karte))
  } catch {
    // Die Ablage ist voll oder gesperrt. Dann gilt der Lauf als offen; ein
    // zweiter Versuch schickt dieselben Teile noch einmal.
  }
}

function istAblaufVorbei(ablauf: unknown, jetzt: number): boolean {
  const wert = Number(ablauf)
  // Eine Stunde Nachsicht: ein Teil, der kurz vor dem Ende abging, darf noch
  // ankommen. Danach beginnt ein neuer, ausdrücklicher Transfer.
  return !Number.isFinite(wert) || wert + 60 * 60_000 < jetzt
}

function prunePlans(karte: Record<string, QuellPlan>, jetzt: number): Record<string, QuellPlan> {
  const behalten = Object.entries(karte)
    .filter(([, plan]) => plan?.v === 1 && !istAblaufVorbei(plan.ablauf, jetzt))
    .sort(([, a], [, b]) => Number(b.begonnenAm) - Number(a.begonnenAm))
    .slice(0, MAX_PLAENE)
  return Object.fromEntries(behalten)
}

function pruneEmpfaenge(
  karte: Record<string, EmpfangsStand>,
  jetzt: number,
): Record<string, EmpfangsStand> {
  const behalten = Object.entries(karte)
    .filter(([, stand]) => stand?.v === 1 && !istAblaufVorbei(stand.ablauf, jetzt))
    .sort(([, a], [, b]) => Number(b.begonnenAm) - Number(a.begonnenAm))
    .slice(0, MAX_EMPFAENGE)
  return Object.fromEntries(behalten)
}

/** Der laufende Plan für ein Zielgerät, oder `null`. */
export function ladeQuelle(konto: number, ziel: string): QuellPlan | null {
  if (!Number.isInteger(konto) || !ziel) return null
  const jetzt = Date.now()
  const karte = liesKarte<QuellPlan>(MESSENGER_KEYS.VERLAUF_TRANSFER)
  const vorher = Object.keys(karte).length
  const geprueft = prunePlans(karte, jetzt)
  if (Object.keys(geprueft).length !== vorher) {
    schreibeKarte(MESSENGER_KEYS.VERLAUF_TRANSFER, geprueft)
  }
  const plan = geprueft[ziel]
  if (!plan || plan.ziel !== ziel || !plan.transferId || !Array.isArray(plan.posten)) return null
  return plan
}

export function speichereQuelle(konto: number, plan: QuellPlan): void {
  if (!Number.isInteger(konto) || !plan?.ziel) return
  const karte = liesKarte<QuellPlan>(MESSENGER_KEYS.VERLAUF_TRANSFER)
  karte[plan.ziel] = plan
  schreibeKarte(MESSENGER_KEYS.VERLAUF_TRANSFER, prunePlans(karte, Date.now()))
}

export function loescheQuelle(konto: number, ziel: string): void {
  if (!Number.isInteger(konto) || !ziel) return
  const karte = liesKarte<QuellPlan>(MESSENGER_KEYS.VERLAUF_TRANSFER)
  if (!(ziel in karte)) return
  delete karte[ziel]
  schreibeKarte(MESSENGER_KEYS.VERLAUF_TRANSFER, karte)
}

/** Der Empfangsfortschritt einer Übertragung, oder `null`. */
export function ladeEmpfang(konto: number, transferId: string): EmpfangsStand | null {
  if (!Number.isInteger(konto) || !transferId) return null
  const jetzt = Date.now()
  const karte = liesKarte<EmpfangsStand>(MESSENGER_KEYS.VERLAUF_EMPFANG)
  const vorher = Object.keys(karte).length
  const geprueft = pruneEmpfaenge(karte, jetzt)
  if (Object.keys(geprueft).length !== vorher) {
    schreibeKarte(MESSENGER_KEYS.VERLAUF_EMPFANG, geprueft)
  }
  const stand = geprueft[transferId]
  if (!stand || stand.transferId !== transferId || !Array.isArray(stand.posten)) return null
  return stand
}

export function speichereEmpfang(konto: number, stand: EmpfangsStand): void {
  if (!Number.isInteger(konto) || !stand?.transferId) return
  const karte = liesKarte<EmpfangsStand>(MESSENGER_KEYS.VERLAUF_EMPFANG)
  karte[stand.transferId] = stand
  schreibeKarte(MESSENGER_KEYS.VERLAUF_EMPFANG, pruneEmpfaenge(karte, Date.now()))
}

/** Ein Bitfeld mit Platz für alle Teile eines Postens. */
export function leereBits(teile: number): string {
  return '0'.repeat(Math.max(0, teile))
}

export function bitGesetzt(bits: string | undefined, teil: number): boolean {
  return bits?.[teil] === '1'
}

/** Setzt ein Bit; ein fehlendes oder zu kurzes Feld wird neu aufgebaut. */
export function setzeBit(bits: string | undefined, teil: number, teile: number): string {
  const grund = typeof bits === 'string' && bits.length === teile ? bits : leereBits(teile)
  return `${grund.slice(0, teil)}1${grund.slice(teil + 1)}`
}

export function bitsVollstaendig(bits: string | undefined, teile: number): boolean {
  return typeof bits === 'string' && bits.length === teile && !bits.includes('0')
}

/** Sind alle Teile aller Posten angenommen? */
export function istVollstaendig(stand: EmpfangsStand): boolean {
  return stand.posten.every((post) => bitsVollstaendig(stand.teile[post.mailbox], post.teile))
}
