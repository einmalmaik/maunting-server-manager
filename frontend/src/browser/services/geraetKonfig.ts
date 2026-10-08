/**
 * Die Gerätekonfiguration aus Rust (`konfig.rs`) als Store, damit Schild und
 * Einstellungen denselben Stand zeigen. Geschrieben wird nur über Rust; der
 * Store übernimmt, was Rust nach dem Speichern zurückgibt.
 */
import { create } from 'zustand'

import { konfig, type BrowserKonfig } from './nativ'

interface KonfigZustand {
  konfig: BrowserKonfig | null
  laden: () => Promise<void>
  aendern: (felder: Partial<BrowserKonfig>) => Promise<void>
}

export const useGeraetKonfig = create<KonfigZustand>()((set) => ({
  konfig: null,
  laden: async () => {
    const geladen = await konfig.laden().catch(() => null)
    if (geladen) set({ konfig: geladen })
  },
  aendern: async (felder) => {
    const neu = await konfig.aendern(felder)
    if (neu) set({ konfig: neu })
  },
}))

/** Der Host einer Adresse ohne `www.`, oder `null` für alles außer http(s). */
export function seitenHost(url: string): string | null {
  try {
    const u = new URL(url)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
    return u.hostname.replace(/^www\./, '').toLowerCase()
  } catch {
    return null
  }
}

/** Dieselbe Regel wie `aktiv_fuer` in `schild/mod.rs`: auch übergeordnete Domains zählen. */
export function schildPausiert(host: string, ausnahmen: string[]): boolean {
  return ausnahmen.some((a) => host === a || host.endsWith(`.${a}`))
}
