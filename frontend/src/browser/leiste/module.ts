/**
 * Was in der Seitenleiste steht und welches Panel dazu aufgeht. Das offene
 * Panel ist die Route des MemoryRouters: so landen auch Sprünge aus den
 * Seiten von MSS (`navigate('/chat')` aus einer Benachrichtigung) im
 * richtigen Panel.
 */
import type { ElementType } from 'react'
import { Bookmark, Bot, Calendar, Code, Download, History, KeyRound, Link2, MessageSquare, Settings, StickyNote } from 'lucide-react'

import { DiscordSymbol, InstagramSymbol, TelegramSymbol, WhatsAppSymbol } from '../marken'
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
  | 'whatsapp'
  | 'discord'
  | 'telegram'
  | 'instagram'
  | 'koppeln'
  | 'entwickler'

export interface Leisteneintrag {
  id: PanelId
  symbol: ElementType
  /** i18n-Schlüssel des Namens. */
  name: string
  /** Gehört zu MSM: braucht die Kopplung, lässt sich ausblenden. */
  modul?: Modul
  /** Arbeitet ohne Panel weiter (lokaler Spiegel). */
  offline?: boolean
  /** Externe Web-App, die im Panel gehostet wird. */
  extern?: string
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
  { id: 'whatsapp', symbol: WhatsAppSymbol, name: 'browser.leiste.whatsapp', extern: 'https://web.whatsapp.com' },
  { id: 'discord', symbol: DiscordSymbol, name: 'browser.leiste.discord', extern: 'https://discord.com/app' },
  { id: 'telegram', symbol: TelegramSymbol, name: 'browser.leiste.telegram', extern: 'https://web.telegram.org' },
  { id: 'instagram', symbol: InstagramSymbol, name: 'browser.leiste.instagram', extern: 'https://www.instagram.com' },
]

/** Kein Panel: die Einstellungen sind eine eigene Seite im Tab (`msb://einstellungen`). */
export const EINSTELLUNGEN_EINTRAG = { symbol: Settings, name: 'browser.leiste.einstellungen' }

export const KOPPELN_EINTRAG: Leisteneintrag = { id: 'koppeln', symbol: Link2, name: 'browser.leiste.koppeln' }

/** Nicht in der Leiste: F12, Strg+Umschalt+I oder „Untersuchen“ im Rechtsklick. */
export const ENTWICKLER_EINTRAG: Leisteneintrag = { id: 'entwickler', symbol: Code, name: 'browser.entwickler.titel' }

/** Die Panel-Kennung zu einem Pfad; Unterpfade der Seiten (`/chat/join/…`) gehören zu ihrem Panel. */
export function panelAusPfad(pfad: string): PanelId | null {
  const erstes = pfad.split('/')[1] ?? ''
  if (erstes === 'user') return 'chat'
  const alle: string[] = [...MSM_EINTRAEGE, ...BROWSER_EINTRAEGE].map((e) => e.id)
  if (erstes === 'koppeln' || erstes === 'entwickler' || alle.includes(erstes)) return erstes as PanelId
  return null
}
