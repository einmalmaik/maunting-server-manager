/**
 * Was die Statistik der Startseite aus den Zählern des Schilds schätzt
 * (`schild/gesamt.rs`). Gezählt wird genau; gesparte Daten und Zeit sind
 * Schätzungen je Seitenaufruf, auf dem etwas geblockt wurde.
 *
 * Die Werte sind der Median einer Messung von Brave über Seitenaufrufe mit
 * und ohne Werbeblocker: knapp 500 KB weniger Daten und 0,5 s weniger
 * Rechenzeit für JavaScript je Seite (Aucinas, Haller, Livshits,
 * „Accurately Predicting Ad Blocker Savings“, 21.10.2019, `QUELLE`).
 */
import type { SchildGesamt } from '../../services/nativ'

export const QUELLE = 'https://brave.com/blog/accurately-predicting-ad-blocker-savings/'
export const JE_SEITE_BYTES = 500_000
export const JE_SEITE_SEKUNDEN = 0.5

export function geschaetzt(g: Pick<SchildGesamt, 'seiten'>): { bytes: number; sekunden: number } {
  return { bytes: g.seiten * JE_SEITE_BYTES, sekunden: g.seiten * JE_SEITE_SEKUNDEN }
}

function einheit(sprache: string, wert: number, unit: string, stellen: number): string {
  return new Intl.NumberFormat(sprache, { style: 'unit', unit, unitDisplay: 'short', maximumFractionDigits: stellen }).format(wert)
}

/** Dezimal wie bei Brave: 1 KB = 1000 Byte. */
export function datenText(bytes: number, sprache: string): string {
  if (bytes >= 1e9) return einheit(sprache, bytes / 1e9, 'gigabyte', 1)
  if (bytes >= 1e6) return einheit(sprache, bytes / 1e6, 'megabyte', bytes >= 1e8 ? 0 : 1)
  return einheit(sprache, bytes / 1e3, 'kilobyte', 0)
}

export function zeitText(sekunden: number, sprache: string): string {
  if (sekunden >= 2 * 86_400) return einheit(sprache, sekunden / 86_400, 'day', 1)
  if (sekunden >= 2 * 3_600) return einheit(sprache, sekunden / 3_600, 'hour', 1)
  if (sekunden >= 120) return einheit(sprache, sekunden / 60, 'minute', 0)
  return einheit(sprache, sekunden, 'second', 1)
}
