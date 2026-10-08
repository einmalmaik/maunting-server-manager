/**
 * Einstellungen, die nur die Oberfläche braucht. Was Rust liest (Schild,
 * Download-Ordner, Panel-Adresse), steht in der Gerätekonfiguration
 * (`konfig.rs`), nicht hier.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import type { SuchmaschinenId } from './searchEngines'

export type Hintergrund = 'schlicht' | 'nacht' | 'wald' | 'tiefsee' | 'eigen'

/** Ein eigenes Bild liegt als data:-URI im localStorage; größer passt dort nicht sicher hinein. */
export const EIGENES_BILD_MAX_BYTES = 3 * 1024 * 1024

export type Modul = 'singra' | 'messenger' | 'notizen' | 'kalender' | 'tresor'

interface EinstellungenZustand {
  suchmaschine: SuchmaschinenId
  searxngUrl: string | null
  hintergrund: Hintergrund
  eigenesBild: string | null
  /** Module der Seitenleiste, die der Nutzer ausgeblendet hat. */
  ausgeblendet: Modul[]
  /** Breite des Seitenpanels in Pixeln. */
  panelBreite: number

  setzen: (teil: Partial<Omit<EinstellungenZustand, 'setzen' | 'modulUmschalten'>>) => void
  modulUmschalten: (modul: Modul) => void
}

export const useEinstellungenStore = create<EinstellungenZustand>()(
  persist(
    (set) => ({
      suchmaschine: 'duckduckgo',
      searxngUrl: null,
      hintergrund: 'schlicht',
      eigenesBild: null,
      ausgeblendet: [],
      panelBreite: 420,

      setzen: (teil) => set(teil),
      modulUmschalten: (modul) =>
        set((s) => ({
          ausgeblendet: s.ausgeblendet.includes(modul)
            ? s.ausgeblendet.filter((m) => m !== modul)
            : [...s.ausgeblendet, modul],
        })),
    }),
    { name: 'msb:einstellungen', version: 1 },
  ),
)
