import type { Plugin } from 'postcss'

/**
 * Im Browser-Bau gelten Breakpoints für den Bereich, in dem eine Seite steht,
 * nicht fürs ganze Fenster.
 *
 * Messenger, Notizen, Kalender und Tresor stehen im MSB in einem Seitenpanel
 * von etwa 420 px. Tailwind fragt die Breite des Fensters ab (`@media`), und
 * bei 1280 px Fenster kam im Panel das Desktop-Layout: vier Notizspalten,
 * zwei Messenger-Spalten, überlappende Kopfzeilen (bis 10/2026).
 *
 * Deshalb werden reine Breitenabfragen zu Containerabfragen auf den Container
 * `msb`, und Viewport-Einheiten zu Container-Einheiten. `body` und der Inhalt
 * des Panels sind je ein solcher Container (`browser/browser.css`): in der
 * Oberfläche gilt also weiter das Fenster, im Panel das Panel. Panel und MSS
 * selbst bleiben unberührt.
 */

const BREITE = String.raw`\((?:min|max)-width:\s*[\d.]+(?:px|rem|em)\)`
const NUR_BREITE = new RegExp(String.raw`^(not all and\s+)?(${BREITE}(?:\s+and\s+${BREITE})*)$`)
const VIEWPORT_EINHEIT = /(\d*\.?\d+)(?:[dsl])?(vh|vw|vmin|vmax)\b/g
const ERSATZ: Record<string, string> = { vh: 'cqh', vw: 'cqw', vmin: 'cqmin', vmax: 'cqmax' }

/** `null`, wenn die Abfrage mehr als Breiten prüft und bleiben muss, wie sie ist. */
export function alsContainerAbfrage(medien: string): string | null {
  const treffer = NUR_BREITE.exec(medien.trim())
  if (!treffer) return null
  if (!treffer[1]) return `msb ${treffer[2]}`
  return treffer[2].includes(' and ') ? `msb not (${treffer[2]})` : `msb not ${treffer[2]}`
}

export function viewportAlsContainer(wert: string): string {
  return wert.replace(VIEWPORT_EINHEIT, (_, zahl: string, einheit: string) => `${zahl}${ERSATZ[einheit]}`)
}

export function breitenAlsContainer(): Plugin {
  return {
    postcssPlugin: 'msb-breiten-als-container',
    AtRule: {
      media(regel) {
        const neu = alsContainerAbfrage(regel.params)
        if (neu) {
          regel.name = 'container'
          regel.params = neu
        }
      },
    },
    Declaration(deklaration) {
      if (/v(h|w|min|max)\b/.test(deklaration.value)) {
        deklaration.value = viewportAlsContainer(deklaration.value)
      }
    },
  }
}
