/**
 * Pfeiltasten in einem Raster aus Kacheln (AGENTS.md Punkt 88): das Raster ist
 * ein Tab-Halt, die Pfeile wandern darin. Kacheln tragen `data-kachel`.
 *
 * Die Kachel, zu der eine Pfeiltaste im Raster führt. Links und rechts gehen
 * in Leserichtung (auch über Monatsgrenzen), hoch und runter in die nächste
 * Zeile zur Kachel, die der Mitte am nächsten liegt. Gemessen wird nur bis zum
 * Ende dieser Zeile, nicht über alle Kacheln.
 */
export function nachbarKachel(raster: HTMLElement, von: HTMLElement, taste: string): HTMLElement | null {
  const alle = [...raster.querySelectorAll<HTMLElement>('[data-kachel]')]
  const i = alle.indexOf(von)
  if (i < 0) return null
  if (taste === 'ArrowLeft') return alle[i - 1] ?? null
  if (taste === 'ArrowRight') return alle[i + 1] ?? null
  if (taste === 'Home') return alle[0] ?? null
  if (taste === 'End') return alle[alle.length - 1] ?? null
  if (taste !== 'ArrowUp' && taste !== 'ArrowDown') return null
  const runter = taste === 'ArrowDown'
  const a = von.getBoundingClientRect()
  const mitte = a.left + a.width / 2
  let zeile: number | null = null
  let beste: HTMLElement | null = null
  let abstand = Infinity
  for (let j = i + (runter ? 1 : -1); j >= 0 && j < alle.length; j += runter ? 1 : -1) {
    const b = alle[j].getBoundingClientRect()
    if (runter ? b.top < a.bottom - 1 : b.bottom > a.top + 1) continue
    if (zeile === null) zeile = b.top
    else if (Math.abs(b.top - zeile) > 1) break
    const d = Math.abs(b.left + b.width / 2 - mitte)
    if (d < abstand) {
      abstand = d
      beste = alle[j]
    }
  }
  return beste
}
