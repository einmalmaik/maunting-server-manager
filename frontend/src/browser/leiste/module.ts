/**
 * Was in der Seitenleiste steht und welches Panel dazu aufgeht. Das offene
 * Panel ist die Route des MemoryRouters: so landen auch Sprünge aus den
 * Seiten von MSS (`navigate('/chat')` aus einer Benachrichtigung) im
 * richtigen Panel.
 */
import type { LucideIcon } from 'lucide-react'
import { Bookmark, Bot, Calendar, Code, Download, History, KeyRound, Link2, MessageSquare, Settings, StickyNote } from 'lucide-react'

import type { Modul } from '../services/einstellungenStore'

export type PanelId =
  | 'ai'
  | 'chat'
  | 'notizen'
  | 'kalender'
  | 'tresor'
  | 'lesezeichen'
  | 'verlauf'
  | 'downloads'
  | 'einstellungen'
  | 'koppeln'
  | 'entwickler'

export interface Leisteneintrag {
  id: PanelId
  symbol: LucideIcon
  /** i18n-Schlüssel des Namens. */
  name: string
  /** Gehört zu MSM: braucht die Kopplung, lässt sich ausblenden. */
  modul?: Modul
  /** Arbeitet ohne Panel weiter (lokaler Spiegel). */
  offline?: boolean
}

export const MSM_EINTRAEGE: Leisteneintrag[] = [
  { id: 'ai', symbol: Bot, name: 'browser.leiste.singra', modul: 'singra' },
  { id: 'chat', symbol: MessageSquare, name: 'browser.leiste.messenger', modul: 'messenger' },
  { id: 'notizen', symbol: StickyNote, name: 'browser.leiste.notizen', modul: 'notizen', offline: true },
  { id: 'kalender', symbol: Calendar, name: 'browser.leiste.kalender', modul: 'kalender', offline: true },
  { id: 'tresor', symbol: KeyRound, name: 'browser.leiste.tresor', modul: 'tresor', offline: true },
]

export const BROWSER_EINTRAEGE: Leisteneintrag[] = [
  { id: 'lesezeichen', symbol: Bookmark, name: 'browser.leiste.lesezeichen' },
  { id: 'verlauf', symbol: History, name: 'browser.leiste.verlauf' },
  { id: 'downloads', symbol: Download, name: 'browser.leiste.downloads' },
]

export const EINSTELLUNGEN_EINTRAG: Leisteneintrag = { id: 'einstellungen', symbol: Settings, name: 'browser.leiste.einstellungen' }

export const KOPPELN_EINTRAG: Leisteneintrag = { id: 'koppeln', symbol: Link2, name: 'browser.leiste.koppeln' }

/** Nicht in der Leiste: F12, Strg+Umschalt+I oder „Untersuchen“ im Rechtsklick. */
export const ENTWICKLER_EINTRAG: Leisteneintrag = { id: 'entwickler', symbol: Code, name: 'browser.entwickler.titel' }

/** Die Panel-Kennung zu einem Pfad; Unterpfade der Seiten (`/chat/join/…`) gehören zu ihrem Panel. */
export function panelAusPfad(pfad: string): PanelId | null {
  const erstes = pfad.split('/')[1] ?? ''
  if (erstes === 'user') return 'chat'
  const alle: string[] = [...MSM_EINTRAEGE, ...BROWSER_EINTRAEGE, EINSTELLUNGEN_EINTRAG].map((e) => e.id)
  if (erstes === 'koppeln' || erstes === 'entwickler' || alle.includes(erstes)) return erstes as PanelId
  return null
}
