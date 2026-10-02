/**
 * Layout für Popover-Tests. jsdom misst jedes Element mit 0 × 0 am Ursprung;
 * damit lässt sich nicht prüfen, ob ein Popover am Fensterrand umklappt.
 *
 * `fakeLayout` setzt die Fenstergröße und gibt dem Auslöser und dem Popover
 * eine Größe. Als Popover gilt jedes Element mit einer der Rollen in `rollen`;
 * es steht dort, wohin sein `style.left`/`style.top` es setzt. Alles andere
 * misst sich wie der Auslöser.
 */
import { vi } from 'vitest'

interface Rechteck {
  left: number
  top: number
  width: number
  height: number
}

function box({ left, top, width, height }: Rechteck): DOMRect {
  return { left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) } as DOMRect
}

export function fakeLayout({
  fenster,
  anker,
  popover,
  rollen = ['menu', 'dialog', 'listbox'],
}: {
  fenster: { breite: number; hoehe: number }
  anker: Rechteck
  popover: { width: number; height: number }
  rollen?: string[]
}) {
  const vorher = { breite: window.innerWidth, hoehe: window.innerHeight }
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: fenster.breite })
  Object.defineProperty(window, 'innerHeight', { configurable: true, writable: true, value: fenster.hoehe })
  const lage = { anker }
  const spion = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (rollen.includes(this.getAttribute('role') ?? '')) {
      return box({ left: parseFloat(this.style.left) || 0, top: parseFloat(this.style.top) || 0, ...popover })
    }
    return box(lage.anker)
  })
  return {
    /** Verschiebt den Auslöser, etwa um Scrollen nachzustellen. */
    ankerNach(neu: Rechteck) {
      lage.anker = neu
    },
    /** Liegt das Popover ganz im Fenster, mit 8 px Rand? */
    imFenster(el: HTMLElement) {
      const left = parseFloat(el.style.left)
      const top = parseFloat(el.style.top)
      return left >= 8 && top >= 8 && left + popover.width <= fenster.breite - 8 && top + popover.height <= fenster.hoehe - 8
    },
    aufraeumen() {
      spion.mockRestore()
      Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: vorher.breite })
      Object.defineProperty(window, 'innerHeight', { configurable: true, writable: true, value: vorher.hoehe })
    },
  }
}
