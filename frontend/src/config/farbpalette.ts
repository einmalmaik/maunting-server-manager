/**
 * Die Auswahlfarben für Kalendereinträge und Notizen.
 *
 * Bis 09/2026 stand diese Liste zweimal da — einmal in `Calendar.tsx`, einmal
 * in `Notes.tsx` —, mit denselben sechs Kennungen, aber verschiedenen
 * Deckkraft-Rezepten und verschiedenen Beschriftungen für dieselbe Farbe
 * („Rot / Rose" gegen „Rot / Dringend", von der die Notizseite ohnehin nur das
 * erste Wort anzeigte). Jetzt steht sie hier, mit einem Namen je Farbe.
 *
 * Hier stehen auch die einzigen Roh-Paletten­farben der Anwendung. Drei der
 * sechs Töne bedeuten nichts — sie sind eine Wahl, die ein Mensch trifft, kein
 * Zustand, den die Software meldet —, und haben deshalb auch keinen
 * Status-Token. `scripts/check-classes.mjs` lässt Rohfarben genau in dieser
 * Datei durch und sonst nirgends.
 *
 * Die Klassennamen stehen ausgeschrieben, nicht zusammengesetzt: Tailwind liest
 * den Quelltext als Text und kennt ein `bg-${ton}/20` nicht.
 */
export interface Farbwahl {
  /** Wird so gespeichert und vom Backend zurückgeliefert. */
  id: string
  /** Schlüssel unter `common.palette.*`. */
  labelKey: string
  /** Zarte Fläche — eine Karte, die den Ton nur andeutet. */
  flaeche: string
  /** Kräftige Fläche — eine Pille, die für sich steht. */
  flaecheStark: string
  text: string
  rand: string
  /** Fertiges Abzeichen: Fläche, Schrift und Rand in einem. */
  pille: string
}

export const FARB_PALETTE: Farbwahl[] = [
  {
    id: 'primary',
    labelKey: 'common.palette.blue',
    flaeche: 'bg-primary/10',
    flaecheStark: 'bg-primary/20',
    text: 'text-primary',
    rand: 'border-primary/40',
    pille: 'bg-primary/20 text-primary border-primary/30',
  },
  {
    id: 'emerald',
    labelKey: 'common.palette.green',
    flaeche: 'bg-status-success/10',
    flaecheStark: 'bg-status-success/20',
    text: 'text-status-success',
    rand: 'border-status-success/40',
    pille: 'bg-status-success/20 text-status-success border-status-success/30',
  },
  {
    id: 'amber',
    labelKey: 'common.palette.yellow',
    flaeche: 'bg-status-warning/10',
    flaecheStark: 'bg-status-warning/20',
    text: 'text-status-warning',
    rand: 'border-status-warning/40',
    pille: 'bg-status-warning/20 text-status-warning border-status-warning/30',
  },
  {
    id: 'rose',
    labelKey: 'common.palette.red',
    flaeche: 'bg-rose-500/10',
    flaecheStark: 'bg-rose-500/20',
    text: 'text-rose-400',
    rand: 'border-rose-500/40',
    pille: 'bg-rose-500/20 text-rose-300 border-rose-500/30',
  },
  {
    id: 'purple',
    labelKey: 'common.palette.purple',
    flaeche: 'bg-purple-500/10',
    flaecheStark: 'bg-purple-500/20',
    text: 'text-purple-400',
    rand: 'border-purple-500/40',
    pille: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  },
  {
    id: 'cyan',
    labelKey: 'common.palette.cyan',
    flaeche: 'bg-cyan-500/10',
    flaecheStark: 'bg-cyan-500/20',
    text: 'text-cyan-400',
    rand: 'border-cyan-500/40',
    pille: 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30',
  },
]

/**
 * Findet die Farbwahl zu einer gespeicherten Kennung. Das Backend kennt noch
 * die älteren Namen `blue` und `green`; sie zeigen auf dieselben Töne.
 */
export function farbwahl(id?: string | null): Farbwahl {
  let gesucht = id || 'primary'
  if (gesucht === 'blue') gesucht = 'primary'
  if (gesucht === 'green') gesucht = 'emerald'
  return FARB_PALETTE.find((f) => f.id === gesucht) || FARB_PALETTE[0]
}
