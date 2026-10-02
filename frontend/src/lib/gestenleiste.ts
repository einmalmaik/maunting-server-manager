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
 * Ohne Import, weil main.tsx vor allem anderen lädt.
 */
interface GestenFenster {
  MsmRand?: { unten(): number }
  __msmGestenleiste?: (px: number) => void
}

export function gestenleisteUebernehmen(fenster: Window = window): void {
  const f = fenster as unknown as GestenFenster
  const setzen = (px: number) => {
    if (Number.isFinite(px) && px >= 0) fenster.document.documentElement.style.setProperty('--msm-gestenleiste', `${px}px`)
  }
  f.__msmGestenleiste = setzen
  const wert = f.MsmRand?.unten()
  if (typeof wert === 'number') setzen(wert)
}
