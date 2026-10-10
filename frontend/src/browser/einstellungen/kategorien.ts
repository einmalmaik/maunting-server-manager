/** Die Kategorien der Einstellungsseite, in der Reihenfolge der Liste. */
import type { LucideIcon } from 'lucide-react'
import { Blocks, Download, Gauge, History, Info, KeyRound, Palette, Puzzle, Search, ShieldBan, ShieldCheck, SlidersHorizontal } from 'lucide-react'

export type Kategorie =
  | 'allgemein'
  | 'suche'
  | 'schutz'
  | 'jugendschutz'
  | 'verlauf'
  | 'passwoerter'
  | 'downloads'
  | 'erweiterungen'
  | 'design'
  | 'leistung'
  | 'dienste'
  | 'ueber'

export const KATEGORIEN: { id: Kategorie; symbol: LucideIcon }[] = [
  { id: 'allgemein', symbol: SlidersHorizontal },
  { id: 'suche', symbol: Search },
  { id: 'schutz', symbol: ShieldCheck },
  { id: 'jugendschutz', symbol: ShieldBan },
  { id: 'verlauf', symbol: History },
  { id: 'passwoerter', symbol: KeyRound },
  { id: 'downloads', symbol: Download },
  { id: 'erweiterungen', symbol: Puzzle },
  { id: 'design', symbol: Palette },
  { id: 'leistung', symbol: Gauge },
  { id: 'dienste', symbol: Blocks },
  { id: 'ueber', symbol: Info },
]

/** Was das Gerät kann: auf Android schlafen Tabs nicht nach Regeln des Browsers, und Erweiterungen kennt die WebView nicht. */
export function sichtbareKategorien(android: boolean) {
  return android ? KATEGORIEN.filter((k) => k.id !== 'leistung' && k.id !== 'erweiterungen') : KATEGORIEN
}

/** Unterseite mit der Datenschutzerklärung, erreichbar aus „Über“. */
export const DATENSCHUTZ_TEIL = 'datenschutzerklaerung'
