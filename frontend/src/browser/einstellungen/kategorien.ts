import type { LucideIcon } from 'lucide-react'
import {
  Blocks,
  Download,
  Gauge,
  History,
  Info,
  KeyRound,
  Palette,
  Puzzle,
  Search,
  Shield,
  ShieldBan,
  ShieldCheck,
  SlidersHorizontal,
} from 'lucide-react'

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
  | 'sicherheit'
  | 'ueber'

export const KATEGORIEN: { id: Kategorie; symbol: LucideIcon; unterpunkt?: boolean }[] = [
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
  { id: 'sicherheit', symbol: Shield, unterpunkt: true },
  { id: 'ueber', symbol: Info },
]

/** Was das Gerät kann: auf Android schlafen Tabs nicht nach Regeln des Browsers, und Erweiterungen kennt die WebView nicht. */
export function sichtbareKategorien(android: boolean, gekoppelt = false) {
  return KATEGORIEN.filter((k) => {
    if (android && (k.id === 'leistung' || k.id === 'erweiterungen')) return false
    if (k.id === 'sicherheit' && !gekoppelt) return false
    return true
  })
}

/** Unterseite mit der Datenschutzerklärung, erreichbar aus „Über“. */
export const DATENSCHUTZ_TEIL = 'datenschutzerklaerung'
