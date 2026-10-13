/**
 * Die Downloads dieser Sitzung. Rust lädt in die Quarantäne, meldet
 * Fortschritt (`download_fortschritt`) und jeden Schritt unter derselben
 * `nr`, lässt den Virenschutz prüfen und legt die Datei dann ab
 * (`downloads/`); die Liste hier ist nur Anzeige und überdauert keinen
 * Neustart.
 *
 * Fortschritt zählt nie als neuer Download: Unbekannte Nummern und
 * abgeschlossene Einträge bleiben unberührt, damit verspätete Messungen
 * keinen Geistereintrag erzeugen und keinen Endstand überschreiben.
 */
import { create } from 'zustand'

import type { DownloadStand, TabEreignis } from './nativ'

/** Stände, nach denen sich nichts mehr ändert. */
const ABSCHLUSS: ReadonlySet<DownloadStand> = new Set(['fertig', 'blockiert', 'fehler', 'speicher', 'abgebrochen'])

export interface Download {
  nr: number
  url: string
  datei: string | null
  stand: DownloadStand
  zeit: number
  /** Empfangene Bytes (letzte Fortschrittsmeldung). */
  empfangen: number | null
  /** Gesamtlänge; null bei unbekannter Länge. */
  gesamt: number | null
  /** Geglättete Rate in Bytes je Sekunde; null ohne brauchbare Messung. */
  bytesProSekunde: number | null
  /** Ungefähre Restzeit in Sekunden; null ohne brauchbare Rate. */
  restSekunden: number | null
  /** Wann sich die Bytezahl zuletzt erhöht hat (ms seit 1970). */
  letzteBytesZeit: number | null
}

export type DownloadEreignis = Extract<TabEreignis, { art: 'download' | 'download_fortschritt' }>

interface DownloadsZustand {
  downloads: Download[]
  /** Neue Downloads seit dem letzten Blick in die Liste. */
  neu: number
  ereignis: (e: DownloadEreignis) => void
  gesehen: () => void
  leeren: () => void
  loeschen: (nr: number) => void
}

function neuerEintrag(e: Extract<TabEreignis, { art: 'download' }>): Download {
  return {
    nr: e.nr,
    url: e.url,
    datei: e.datei,
    stand: e.stand,
    zeit: Date.now(),
    empfangen: null,
    gesamt: null,
    bytesProSekunde: null,
    restSekunden: null,
    letzteBytesZeit: null,
  }
}

export const useDownloadsStore = create<DownloadsZustand>()((set) => ({
  downloads: [],
  neu: 0,

  ereignis: (e) =>
    set((s) => {
      const index = s.downloads.findIndex((d) => d.nr === e.nr)
      if (e.art === 'download_fortschritt') {
        if (index < 0) return s
        const ziel = s.downloads[index]
        if (ABSCHLUSS.has(ziel.stand)) return s
        const waechst = e.empfangen > (ziel.empfangen ?? -1)
        const downloads = [...s.downloads]
        downloads[index] = {
          ...ziel,
          empfangen: e.empfangen,
          gesamt: e.gesamt,
          bytesProSekunde: e.bytes_pro_sekunde,
          restSekunden: e.rest_sekunden,
          letzteBytesZeit: waechst ? Date.now() : (ziel.letzteBytesZeit ?? Date.now()),
        }
        return { downloads }
      }
      if (index < 0) {
        return {
          downloads: [neuerEintrag(e), ...s.downloads],
          neu: s.neu + 1,
        }
      }
      const downloads = [...s.downloads]
      downloads[index] = { ...downloads[index], stand: e.stand, datei: e.datei ?? downloads[index].datei }
      return { downloads }
    }),

  gesehen: () => set({ neu: 0 }),
  leeren: () =>
    set((s) => ({ downloads: s.downloads.filter((d) => !ABSCHLUSS.has(d.stand)) })),
  loeschen: (nr: number) => set((s) => ({ downloads: s.downloads.filter((d) => d.nr !== nr) })),
}))
