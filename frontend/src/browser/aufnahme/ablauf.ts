/**
 * Ein Screenshot vom Klick bis zur Datei: aufnehmen (Rust, `tabs/aufnahme.rs`),
 * bei „Bereich auswählen“ zuschneiden, dann kopieren oder speichern. Das Bild
 * liegt nur im Speicher der Oberfläche (Blob) und fällt beim Schließen weg.
 */
import { create } from 'zustand'

import { nativ } from '../services/nativ'
import type { Tab } from '../services/tab'
import { seiteFrei } from '../services/ueberdeckung'
import { base64AusBlob, dateiname, pngAusBase64, zuschneiden, type Ausschnitt } from './bild'

export type Art = 'sichtbar' | 'auswahl' | 'ganz'

interface Bild {
  tab: string
  /** Die Seite, von der das Bild stammt: für Dateiname und Download-Liste. */
  url: string
  bild: Blob
  /** Blob-Adresse für die Anzeige. */
  adresse: string
}

export type Stand =
  | { art: 'laeuft' }
  | ({ art: 'auswahl' } & Bild)
  | ({ art: 'ergebnis'; abgeschnitten: boolean } & Bild)
  | { art: 'fehler' }

interface Zustand {
  stand: Stand | null
  aufnehmen: (tab: Pick<Tab, 'id' | 'url'>, art: Art) => Promise<void>
  /** Schneidet das Bild der Auswahl zu; ohne Ausschnitt bleibt es ganz. */
  auswaehlen: (ausschnitt?: Ausschnitt) => Promise<void>
  /** Legt die Datei ab und gibt ihren Namen (unter Windows mit Pfad) zurück. */
  speichern: () => Promise<string>
  schliessen: () => void
}

function setzen(set: (s: Partial<Zustand>) => void, alt: Stand | null, neu: Stand | null) {
  if (alt && 'adresse' in alt && (!neu || !('adresse' in neu) || neu.adresse !== alt.adresse)) URL.revokeObjectURL(alt.adresse)
  set({ stand: neu })
}

export const useAufnahme = create<Zustand>()((set, get) => ({
  stand: null,

  aufnehmen: async (tab, art) => {
    setzen(set, get().stand, { art: 'laeuft' })
    try {
      // Das Menü, aus dem der Klick kam, verdeckt die Seite noch.
      if (!(await seiteFrei())) throw new Error('verdeckt')
      const aufnahme = await nativ.tabAufnahme(tab.id, art === 'ganz')
      if (!aufnahme) throw new Error('nativ')
      if (get().stand?.art !== 'laeuft') return
      const bild = pngAusBase64(aufnahme.png)
      const basis = { tab: tab.id, url: tab.url, bild, adresse: URL.createObjectURL(bild) }
      setzen(set, get().stand, art === 'auswahl' ? { art: 'auswahl', ...basis } : { art: 'ergebnis', abgeschnitten: aufnahme.abgeschnitten, ...basis })
    } catch {
      if (get().stand?.art === 'laeuft') setzen(set, get().stand, { art: 'fehler' })
    }
  },

  auswaehlen: async (ausschnitt) => {
    const s = get().stand
    if (s?.art !== 'auswahl') return
    const bild = ausschnitt ? await zuschneiden(s.bild, ausschnitt) : s.bild
    if (get().stand !== s) return
    setzen(set, s, { ...s, art: 'ergebnis', abgeschnitten: false, bild, adresse: bild === s.bild ? s.adresse : URL.createObjectURL(bild) })
  },

  speichern: async () => {
    const s = get().stand
    if (s?.art !== 'ergebnis') throw new Error('kein Bild')
    const datei = await nativ.aufnahmeSpeichern(s.tab, dateiname(s.url, new Date()), s.url, await base64AusBlob(s.bild))
    if (!datei) throw new Error('nativ')
    return datei
  },

  schliessen: () => setzen(set, get().stand, null),
}))
