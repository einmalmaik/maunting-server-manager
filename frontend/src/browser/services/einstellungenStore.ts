/**
 * Einstellungen, die nur die Oberfläche braucht. Was Rust liest (Schild,
 * Download-Ordner, Panel-Adresse), steht in der Gerätekonfiguration
 * (`konfig.rs`), nicht hier.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { ablage, passend } from './ablage'
import { wirksameSuche, type SuchmaschinenId } from './searchEngines'
import { istGekoppelt, useSitzung } from './sitzung'

export type Hintergrund = 'schlicht' | 'nacht' | 'wald' | 'tiefsee' | 'eigen'

/** Ein eigenes Bild liegt als data:-URI im localStorage; größer passt dort nicht sicher hinein. */
export const EIGENES_BILD_MAX_BYTES = 3 * 1024 * 1024

export type Modul = 'singra' | 'messenger' | 'notizen' | 'kalender' | 'tresor'

/** Was in Leiste oder Menü steht und sich ordnen lässt: die Module und die Listen des Browsers. */
export type Leistenziel = Modul | 'lesezeichen' | 'verlauf' | 'downloads'

/** Knöpfe in Kopf- und Navigationsleiste, die man ausblenden kann. */
export type Knopf = 'neuLaden' | 'schild' | 'stern' | 'privaterTab'
export const KNOEPFE: Knopf[] = ['neuLaden', 'schild', 'stern', 'privaterTab']

export type Ausblendbar = Leistenziel | Knopf

/**
 * Wo die Einträge stehen: hinter dem Menüknopf (☰) in der Navigationsleiste,
 * als Leiste links oder rechts, oder als kleine Symbole oben.
 */
export type Leistenort = 'menue' | 'links' | 'rechts' | 'oben'

/** Eine Kachel der Startseite. */
export interface Schnellzugriff {
  url: string
  titel: string
}

/** Mehr Kacheln passen nicht sinnvoll auf die Startseite. */
export const SCHNELLZUGRIFFE_MAX = 24

export const VORGABE_SCHNELLZUGRIFFE: Schnellzugriff[] = [
  { url: 'https://www.wikipedia.org/', titel: 'Wikipedia' },
  { url: 'https://www.youtube.com/', titel: 'YouTube' },
  { url: 'https://github.com/', titel: 'GitHub' },
  { url: 'https://www.reddit.com/', titel: 'Reddit' },
]

/** Kästen unter den Schnellzugriffen; Termine und Notizen nur gekoppelt. */
export type Widget = 'uhr' | 'termine' | 'notizen' | 'zuletzt' | 'nachrichten'
export const WIDGETS: Widget[] = ['uhr', 'termine', 'notizen', 'zuletzt', 'nachrichten']

/** Themen der Nachrichten; die Suchbegriffe dazu stehen im Backend (`browser_nachrichten_service.THEMEN`). */
export type NachrichtenThema = 'technik' | 'gaming' | 'politik' | 'wirtschaft' | 'wissenschaft' | 'sport'
export const NACHRICHTEN_THEMEN: NachrichtenThema[] = ['technik', 'gaming', 'politik', 'wirtschaft', 'wissenschaft', 'sport']

/** Nach wie vielen Minuten im Hintergrund ein Tab schläft; 0 heißt nie. */
export const SCHLAFEN_NACH = [5, 15, 30, 60, 0] as const
export type SchlafenNach = (typeof SCHLAFEN_NACH)[number]

/** Wie lange der Verlauf bleibt; Älteres fällt beim Start und stündlich weg (`useVerlaufFrist`). */
export type VerlaufFrist = 'immer' | 'woche' | 'monat' | 'halbjahr' | 'jahr'

interface EinstellungenZustand {
  /** `null`: keine eigene Wahl; welche Suche dann gilt, sagt `wirksameSuche`. */
  suchmaschine: SuchmaschinenId | null
  searxngUrl: string | null
  hintergrund: Hintergrund
  eigenesBild: string | null
  /** Was der Nutzer ausgeblendet hat: Einträge und Knöpfe. */
  ausgeblendet: Ausblendbar[]
  leiste: Leistenort
  /** Auf welcher Seite der Seite das Panel aufgeht. */
  panelSeite: 'links' | 'rechts'
  /** Eigene Reihenfolge der Einträge; was fehlt, steht dahinter in der Grundordnung. */
  anordnung: Leistenziel[]
  /** Breite des Seitenpanels in Pixeln. */
  panelBreite: number
  /** Nach dem Start: die Tabs vom letzten Mal oder eine leere Startseite. */
  beimStart: 'letzte' | 'startseite'
  verlaufBehalten: VerlaufFrist
  /** Gespeicherte Anmeldungen einfügen und nach dem Absenden speichern anbieten. */
  ausfuellen: boolean
  /** Bei Registrierung und Passwortwechsel selbst ein starkes Passwort einsetzen. */
  erzeugen: boolean
  zahlungen: boolean
  schnellzugriffe: Schnellzugriff[]
  /** Eigene Reihenfolge der Widgets; was fehlt, folgt in der Grundordnung. */
  widgetOrdnung: Widget[]
  /** „Zuletzt besucht“ ist anfangs aus: die Startseite zeigt sonst den Verlauf jedem, der mitschaut. */
  widgetsAus: Widget[]
  /** Welche Themen das Nachrichten-Widget zeigt; gefiltert wird nur hier, der Server liefert allen dasselbe. */
  nachrichtenThemen: NachrichtenThema[]
  /** Schlagworte, durch Komma getrennt; leer heißt alles aus den Themen. */
  nachrichtenWorte: string
  schlafenNach: SchlafenNach
  /** Tabs, die eine Stunde im Hintergrund lagen, geben ihren Speicher ganz frei und laden beim Zeigen neu. */
  speicherSparen: boolean
  /** Hosts, deren Tabs nie schlafen. */
  schlafAusnahmen: string[]
  /** Am Handy: das Angebot, das Such-Widget auf den Startbildschirm zu legen, ist erledigt. */
  widgetAngeboten: boolean
  /** Unter Windows kurz nach dem Start bei GitHub nach einer neuen Version fragen. */
  updatesSuchen: boolean

  setzen: (teil: Partial<Omit<EinstellungenZustand, 'setzen' | 'umschalten'>>) => void
  umschalten: (was: Ausblendbar) => void
}

/**
 * Bis zu eigenen Kacheln zeigte die Startseite die ersten acht Lesezeichen.
 * Wer welche hatte, behält sie als Kacheln, statt dass dort Fremdes steht.
 */
function bisherigeKacheln(): Schnellzugriff[] | null {
  try {
    const lesezeichen = JSON.parse(localStorage.getItem('msb:verlauf') ?? 'null')?.state?.lesezeichen
    if (!Array.isArray(lesezeichen) || lesezeichen.length === 0) return null
    return lesezeichen
      .filter((l): l is Schnellzugriff => typeof l?.url === 'string' && typeof l?.titel === 'string')
      .slice(0, 8)
      .map(({ url, titel }) => ({ url, titel }))
  } catch {
    return null
  }
}

export const useEinstellungenStore = create<EinstellungenZustand>()(
  persist(
    (set) => ({
      suchmaschine: null,
      searxngUrl: null,
      hintergrund: 'schlicht',
      eigenesBild: null,
      ausgeblendet: [],
      leiste: 'menue',
      panelSeite: 'rechts',
      anordnung: [],
      panelBreite: 420,
      beimStart: 'letzte',
      verlaufBehalten: 'immer',
      ausfuellen: true,
      erzeugen: true,
      zahlungen: true,
      schnellzugriffe: VORGABE_SCHNELLZUGRIFFE,
      widgetOrdnung: [],
      widgetsAus: ['zuletzt'],
      nachrichtenThemen: NACHRICHTEN_THEMEN,
      nachrichtenWorte: '',
      schlafenNach: 30,
      speicherSparen: false,
      schlafAusnahmen: [],
      widgetAngeboten: false,
      updatesSuchen: true,

      setzen: (teil) => set(teil),
      umschalten: (was) =>
        set((s) => ({
          ausgeblendet: s.ausgeblendet.includes(was) ? s.ausgeblendet.filter((m) => m !== was) : [...s.ausgeblendet, was],
        })),
    }),
    // Neue Felder brauchen keine neue Version: was der gespeicherte Stand
    // nicht kennt, kommt aus den Vorgaben oben (flaches Zusammenführen).
    {
      name: 'msb:einstellungen',
      version: 2,
      storage: ablage,
      // Bis Version 1 war DuckDuckGo die Vorgabe und stand gespeichert da wie
      // eine eigene Wahl. Es wird zur Werkseinstellung, damit ein gekoppelter
      // Browser über den eigenen Server sucht.
      migrate: (gespeichert, version) => {
        const g = gespeichert as Record<string, unknown> | null
        if (version < 2 && g && g.suchmaschine === 'duckduckgo') return { ...g, suchmaschine: null }
        return g
      },
      merge: (gespeichert, aktuell) => {
        const g = passend(aktuell, gespeichert)
        return { ...aktuell, ...g, schnellzugriffe: g.schnellzugriffe ?? bisherigeKacheln() ?? aktuell.schnellzugriffe }
      },
    },
  ),
)

/** Die Suche, die gerade gilt (`wirksameSuche`), außerhalb von React. */
export function wirksameSucheJetzt(): SuchmaschinenId {
  return wirksameSuche(useEinstellungenStore.getState().suchmaschine, istGekoppelt(useSitzung.getState().stand))
}

export function useWirksameSuche(): SuchmaschinenId {
  const gewaehlt = useEinstellungenStore((s) => s.suchmaschine)
  const gekoppelt = useSitzung((s) => istGekoppelt(s.stand))
  return wirksameSuche(gewaehlt, gekoppelt)
}
