/**
 * Was von den Tabs einen Neustart überdauert: normale Tabs mit Adresse, Titel
 * und Favicon, nie private. Gelesen wird nur, was `partialize` schreibt, mit
 * dem richtigen Typ (`merge`).
 */
import type { PersistOptions } from 'zustand/middleware'

import { ablage, ADRESSE_MAX, kurzerTitel, passend } from './ablage'
import { leererTab } from './tab'
import type { TabsZustand } from './tabsStore'

export const tabsSpeichern: PersistOptions<TabsZustand, unknown> = {
  name: 'msb:tabs',
  version: 1,
  storage: ablage,
  partialize: (s) => {
    const tabs = s.tabs
      .filter((t) => !t.privat)
      .map((t) => ({
        ...leererTab(t.id),
        // Zu lang (eine Seite kann das): beim nächsten Start nur die Herkunft.
        url: t.url.length > ADRESSE_MAX ? herkunftVon(t.url) : t.url,
        titel: kurzerTitel(t.titel),
        favicon: t.favicon && t.favicon.length <= FAVICON_MAX ? t.favicon : null,
      }))
    const aktivId = tabs.some((t) => t.id === s.aktivId) ? s.aktivId : tabs[0]?.id
    return { tabs, aktivId }
  },
  merge: (gespeichert, aktuell) => {
    const g = (gespeichert ?? {}) as { tabs?: unknown; aktivId?: unknown }
    // Nur Tabs mit gültiger Kennung und von ihnen nur, was `partialize`
    // schreibt, mit dem richtigen Typ; der Rest kommt aus dem leeren Tab.
    const tabs = (Array.isArray(g.tabs) ? g.tabs : [])
      .filter((t): t is { id: string } => typeof t?.id === 'string' && /^tab-[A-Za-z0-9-]{1,40}$/.test(t.id))
      .map((t) => ({ ...leererTab(t.id), ...passend({ url: '', titel: '', favicon: null as string | null }, t) }))
    const aktivId = tabs.find((t) => t.id === g.aktivId)?.id
    return aktivId ? { ...aktuell, tabs, aktivId } : aktuell
  },
}

/** Ein Favicon (unter Android eine Data-Adresse) wird nur bis zu dieser Länge gespeichert. */
const FAVICON_MAX = 64 * 1024

function herkunftVon(url: string): string {
  try {
    return `${new URL(url).origin}/`
  } catch {
    return ''
  }
}
