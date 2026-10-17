/**
 * Zustand und Einstellungen für den puristischen Lesemodus (Reader View).
 * Schriftgröße, Schriftart und Zeilenbreite werden persistent gespeichert;
 * der Artikel-Cache verbleibt im Arbeitsspeicher, um den lokalen Speicher nicht zu überlasten.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { ablage, passend } from './ablage'
import type { ExtrahierterArtikel } from './lesemodusService'

export type Schriftgroesse = 'klein' | 'mittel' | 'gross' | 'sehr_gross'
export type Schriftart = 'sans' | 'serif' | 'mono'
export type Zeilenbreite = 'schmal' | 'standard' | 'breit'
export type LinksModus = 'dezent' | 'hervorgehoben' | 'aus'

export interface LesemodusZustand {
  schriftgroesse: Schriftgroesse
  schriftart: Schriftart
  breite: Zeilenbreite
  linksModus: LinksModus
  artikelCache: Record<string, ExtrahierterArtikel>

  setSchriftgroesse: (groesse: Schriftgroesse) => void
  setSchriftart: (art: Schriftart) => void
  setBreite: (breite: Zeilenbreite) => void
  setLinksModus: (modus: LinksModus) => void
  artikelCachen: (key: string, artikel: ExtrahierterArtikel) => void
  artikelAusCache: (key: string) => ExtrahierterArtikel | undefined
  cacheLeeren: (key?: string) => void
}

export const useLesemodusStore = create<LesemodusZustand>()(
  persist(
    (set, get) => ({
      schriftgroesse: 'mittel',
      schriftart: 'sans',
      breite: 'standard',
      linksModus: 'dezent',
      artikelCache: {},

      setSchriftgroesse: (groesse) => set({ schriftgroesse: groesse }),
      setSchriftart: (art) => set({ schriftart: art }),
      setBreite: (breite) => set({ breite }),
      setLinksModus: (modus) => set({ linksModus: modus }),

      artikelCachen: (key, artikel) =>
        set((s) => ({
          artikelCache: { ...s.artikelCache, [key]: artikel },
        })),

      artikelAusCache: (key) => get().artikelCache[key],

      cacheLeeren: (key) =>
        set((s) => {
          if (!key) return { artikelCache: {} }
          const { [key]: _, ...rest } = s.artikelCache
          return { artikelCache: rest }
        }),
    }),
    {
      name: 'msb:lesemodus',
      storage: ablage,
      partialize: (s) => ({
        schriftgroesse: s.schriftgroesse,
        schriftart: s.schriftart,
        breite: s.breite,
        linksModus: s.linksModus,
      }),
      merge: (gespeichert, aktuell) => ({
        ...aktuell,
        ...passend(aktuell, gespeichert),
        artikelCache: {},
      }),
    },
  ),
)
