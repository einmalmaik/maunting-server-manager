/**
 * Gestenleiste unter Android, auch wenn der WebView sie nicht meldet.
 *
 * Die App zeichnet randlos. Ältere Android-WebViews (im Emulator 124) geben für
 * env(safe-area-inset-bottom) 0 aus, obwohl unten die Gesten- bzw.
 * Navigationsleiste liegt; Fußleisten verschwanden darunter. Die App reicht
 * deren Höhe deshalb selbst herein (MainActivity.kt: `MsmRand.unten()` beim
 * Start, `__msmGestenleiste` bei jeder Änderung), und `--msm-unten-sicher` in
 * index.css nimmt das Größere von beidem. Wo der WebView die Leiste meldet,
 * sind beide Werte gleich, es wird nichts doppelt.
 *
 * Dasselbe gilt für die übrigen Ränder, die der Browser am Handy braucht:
 * im Querformat meldet env(safe-area-inset-top) 0, obwohl oben die
 * Statusleiste steht, und die Navigationsleiste mit drei Knöpfen liegt dann
 * seitlich. Die App reicht sie über `MsmRand.oben()/links()/rechts()` und
 * `__msmRand(seite, px)` herein; `--msm-oben-sicher`, `--msm-links-sicher`
 * und `--msm-rechts-sicher` in index.css nehmen wieder das Größere.
 *
 * Ohne Import, weil main.tsx vor allem anderen lädt.
 */
type Seite = 'oben' | 'links' | 'rechts'

interface GestenFenster {
  MsmRand?: { unten(): number } & Partial<Record<Seite, () => number>>
  __msmGestenleiste?: (px: number) => void
  __msmRand?: (seite: Seite, px: number) => void
}

const VARIABLE: Record<Seite, string> = { oben: '--msm-statusleiste', links: '--msm-rand-links', rechts: '--msm-rand-rechts' }

export function gestenleisteUebernehmen(fenster: Window = window): void {
  const f = fenster as unknown as GestenFenster
  const stil = fenster.document.documentElement.style
  const setzen = (variable: string, px: number) => {
    if (Number.isFinite(px) && px >= 0) stil.setProperty(variable, `${px}px`)
  }
  f.__msmGestenleiste = (px) => setzen('--msm-gestenleiste', px)
  f.__msmRand = (seite, px) => {
    if (seite in VARIABLE) setzen(VARIABLE[seite], px)
  }
  const wert = f.MsmRand?.unten()
  if (typeof wert === 'number') setzen('--msm-gestenleiste', wert)
  for (const seite of Object.keys(VARIABLE) as Seite[]) {
    const rand = f.MsmRand?.[seite]?.()
    if (typeof rand === 'number') setzen(VARIABLE[seite], rand)
  }
}
