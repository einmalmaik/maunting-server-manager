/**
 * Die Downloads dieser Sitzung. Rust lädt in die Quarantäne, lässt den
 * Virenschutz prüfen und legt die Datei dann ab (`downloads/`); es meldet
 * jeden Schritt unter derselben `nr`. Die Liste hier ist nur Anzeige und
 * überdauert keinen Neustart.
 */
import { create } from 'zustand'

import type { DownloadStand, TabEreignis } from './nativ'

export interface Download {
  nr: number
  url: string
  datei: string | null
  stand: DownloadStand
  zeit: number
}

interface DownloadsZustand {
  downloads: Download[]
  /** Neue Downloads seit dem letzten Blick in die Liste. */
  neu: number
  ereignis: (e: Extract<TabEreignis, { art: 'download' }>) => void
  gesehen: () => void
  leeren: () => void
}

export const useDownloadsStore = create<DownloadsZustand>()((set) => ({
  downloads: [],
  neu: 0,

  ereignis: (e) =>
    set((s) => {
      const index = s.downloads.findIndex((d) => d.nr === e.nr)
      if (index < 0) {
        return {
          downloads: [{ nr: e.nr, url: e.url, datei: e.datei, stand: e.stand, zeit: Date.now() }, ...s.downloads],
          neu: s.neu + 1,
        }
      }
      const downloads = [...s.downloads]
      downloads[index] = { ...downloads[index], stand: e.stand, datei: e.datei ?? downloads[index].datei }
      return { downloads }
    }),

  gesehen: () => set({ neu: 0 }),
  leeren: () => set((s) => ({ downloads: s.downloads.filter((d) => d.stand === 'start' || d.stand === 'pruefung') })),
}))
