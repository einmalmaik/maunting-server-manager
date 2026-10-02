/**
 * Größen, Zeitpunkte und Dauern, wie sie überall in der Oberfläche stehen.
 *
 * Bis 02.10.2026 gab es die Bytes fünfmal (Dateimanager, Postgres-Studio, zwei
 * im Messenger, Aufräumkarte), jedes Mal mit eigenen Stufen: im Messenger
 * endete die Skala bei MB, eine unbekannte Größe hieß dort „0 B“. Die Dauer
 * eines Videos, einer Sprachnachricht und eines Anrufs sah dreimal anders aus.
 */
import type { PanelTimeFormat } from '@/utils/timeFormat'

const KIB = 1024
const MIB = KIB * 1024
const GIB = MIB * 1024
const TIB = GIB * 1024

/**
 * „512 B“, „1.5 KB“, „3.00 GB“. Ist keine Größe bekannt (fehlt, negativ, keine
 * Zahl), steht ein Strich; „0 B“ heißt wirklich leer.
 */
const STUFEN = [
  { name: 'KB', teiler: KIB, stellen: 1 },
  { name: 'MB', teiler: MIB, stellen: 1 },
  { name: 'GB', teiler: GIB, stellen: 2 },
  { name: 'TB', teiler: TIB, stellen: 2 },
] as const

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '-'
  if (Math.round(bytes) < KIB) return `${Math.round(bytes)} B`
  // Gerundet wird erst hier: 1048575 B wären sonst „1024.0 KB“ statt „1.0 MB“.
  for (const [i, { name, teiler, stellen }] of STUFEN.entries()) {
    const wert = (bytes / teiler).toFixed(stellen)
    if (Number(wert) < 1024 || i === STUFEN.length - 1) return `${wert} ${name}`
  }
  return '-'
}

export interface ZeitpunktOptionen {
  /** Die 12/24-Stunden-Einstellung des Panels; ohne sie gilt die Sprache. */
  zeitformat?: PanelTimeFormat
  /**
   * Die Zeit steht schon als Ortszeit in UTC (Aufnahmezeit aus EXIF): so
   * zeigen, wie sie dasteht, nicht in die Zeitzone des Geräts umrechnen.
   */
  utc?: boolean
}

/** „02.10.2026, 14:05“ in der Sprache der App. */
export function formatZeitpunkt(wann: number | Date, sprache: string, optionen: ZeitpunktOptionen = {}): string {
  const datum = wann instanceof Date ? wann : new Date(wann)
  if (Number.isNaN(datum.getTime())) return '-'
  return new Intl.DateTimeFormat(sprache, {
    dateStyle: 'medium',
    timeStyle: 'short',
    hour12: optionen.zeitformat ? optionen.zeitformat === '12h' : undefined,
    timeZone: optionen.utc ? 'UTC' : undefined,
  }).format(datum)
}

/** Dauer eines Videos, einer Sprachnachricht, eines Anrufs: „0:07“, „12:34“, „1:02:03“. */
export function formatDauer(sekunden: number): string {
  const s = Number.isFinite(sekunden) ? Math.max(0, Math.floor(sekunden)) : 0
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const rest = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`
}
