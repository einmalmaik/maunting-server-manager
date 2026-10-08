/**
 * Die Downloads dieser Sitzung. Rust legt die Datei in den gewählten Ordner
 * (`downloads.rs`) und meldet Start und Ende; die Liste hier ist nur Anzeige
 * und überdauert keinen Neustart.
 */
import { create } from 'zustand'

import type { DownloadStand, TabEreignis } from './nativ'

export interface Download {
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
      if (e.stand === 'start') {
        return {
          downloads: [{ url: e.url, datei: e.datei, stand: 'start', zeit: Date.now() }, ...s.downloads],
          neu: s.neu + 1,
        }
      }
      // Das Ende gehört zum jüngsten laufenden Download derselben Adresse.
      const index = s.downloads.findIndex((d) => d.url === e.url && d.stand === 'start')
      if (index < 0) return s
      const downloads = [...s.downloads]
      downloads[index] = { ...downloads[index], stand: e.stand, datei: e.datei ?? downloads[index].datei }
      return { downloads }
    }),

  gesehen: () => set({ neu: 0 }),
  leeren: () => set((s) => ({ downloads: s.downloads.filter((d) => d.stand === 'start') })),
}))
