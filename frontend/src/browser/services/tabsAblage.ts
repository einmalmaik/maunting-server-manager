/**
 * Was von den Tabs einen Neustart überdauert: normale Tabs mit Adresse, Titel
 * und Favicon, nie private. Gelesen wird nur, was `partialize` schreibt, mit
 * dem richtigen Typ (`merge`).
 */
import { createJSONStorage, type PersistOptions } from 'zustand/middleware'

import { ADRESSE_MAX, kurzerTitel, lokalerSpeicher, passend } from './ablage'
import { aufPontis } from './intern'
import { leererTab } from './tab'
import type { TabsZustand } from './tabsStore'

let unterfensterAktiv = false

export function setzeUnterfenster(aktiv: boolean): void {
  unterfensterAktiv = aktiv
}

export function istUnterfenster(): boolean {
  return unterfensterAktiv
}

export const tabsSpeichern: PersistOptions<TabsZustand, unknown> = {
  name: 'msb:tabs',
  version: 2,
  // v1 kannte keine Arbeitsbereiche und kein `pontis://`; `merge` ergänzt und
  // bereinigt die Felder. Ohne `migrate` verwürfe Zustand den ganzen Stand.
  migrate: (gespeichert) => gespeichert,
  storage: createJSONStorage(() => ({
    getItem: (name: string) => (unterfensterAktiv ? null : lokalerSpeicher.getItem(name)),
    setItem: (name: string, wert: string) => {
      if (unterfensterAktiv) return
      lokalerSpeicher.setItem(name, wert)
    },
    removeItem: (name: string) => {
      if (unterfensterAktiv) return
      lokalerSpeicher.removeItem(name)
    },
  })),
  partialize: (s) => {
    if (unterfensterAktiv) {
      return {}
    }
    const tabs = s.tabs
      .filter((t) => !t.privat)
      .map((t) => ({
        ...leererTab(t.id, false, t.workspaceId),
        // Zu lang (eine Seite kann das): beim nächsten Start nur die Herkunft.
        url: t.url.length > ADRESSE_MAX ? herkunftVon(t.url) : t.url,
        titel: kurzerTitel(t.titel),
        favicon: t.favicon && t.favicon.length <= FAVICON_MAX ? t.favicon : null,
        workspaceId: t.workspaceId,
        elternId: t.elternId,
      }))
    const aktivId = tabs.some((t) => t.id === s.aktivId) ? s.aktivId : tabs[0]?.id
    return { tabs, aktivId }
  },
  merge: (gespeichert, aktuell) => {
    if (unterfensterAktiv) {
      return aktuell
    }
    const g = (gespeichert ?? {}) as { tabs?: unknown; aktivId?: unknown }
    // Nur Tabs mit gültiger Kennung und von ihnen nur, was `partialize`
    // schreibt, mit dem richtigen Typ; der Rest kommt aus dem leeren Tab.
    const tabs = (Array.isArray(g.tabs) ? g.tabs : [])
      .filter((t): t is { id: string } => typeof t?.id === 'string' && /^tab-[A-Za-z0-9-]{1,40}$/.test(t.id))
      .map((t) => {
        const geladen = {
          ...leererTab(t.id),
          ...passend(
            {
              url: '',
              titel: '',
              favicon: null as string | null,
              workspaceId: 'standard',
              elternId: null as string | null,
            },
            t
          ),
        }
        return {
          ...geladen,
          url: aufPontis(geladen.url),
          // `passend` lässt bei einer Vorgabe `null` jeden einfachen Wert zu;
          // diese beiden Felder sind aber nie Zahlen oder Wahrheitswerte.
          favicon: typeof geladen.favicon === 'string' ? geladen.favicon : null,
          elternId: typeof geladen.elternId === 'string' ? geladen.elternId : null,
        }
      })
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
