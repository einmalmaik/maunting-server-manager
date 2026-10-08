/**
 * Der Name eines Symbols der Leiste, auf der Seite zur Seite hin: rechts
 * daneben, steht die Leiste rechts, links daneben.
 *
 * Rechts der Leiste liegt die Seite, ein eigenes Fenster über der Oberfläche:
 * eine `Kurzinfo` dort wäre unsichtbar. Windows zeichnet die Blase deshalb
 * selbst (`kurzinfo.rs`), in den Farben der Kurzinfo. Sie erscheint wie diese
 * beim Überfahren mit der Maus und beim Tastaturfokus, nie bei Berührung.
 */
import { useEffect, type FocusEvent, type PointerEvent } from 'react'

import { nativ } from '../services/nativ'

/** So lange liegt die Maus auf einem Symbol, bevor sein Name kommt. */
export const VERZOEGERUNG_MS = 400
/** Abstand zwischen Symbol und Blase. */
const ABSTAND = 8
/** Zwischen zwei Symbolen bleibt die Blase so lange stehen und wandert dann weiter. */
const NACHLAUF_MS = 120

type Rgb = [number, number, number]
interface Farben {
  hintergrund: Rgb
  schrift: Rgb
  rand: Rgb
}

let farben: Farben | null = null

function rgb(wert: string): Rgb {
  const [r = 0, g = 0, b = 0] = (wert.match(/\d+(\.\d+)?/g) ?? []).map(Number)
  return [r, g, b]
}

/** Die Farben der Kurzinfo, einmal aus den Design-Tokens gelesen. */
function farbenLesen(): Farben {
  if (farben) return farben
  const probe = document.createElement('span')
  probe.className = 'hidden border bg-surface-container-highest text-on-surface border-outline-variant'
  document.body.append(probe)
  const stil = getComputedStyle(probe)
  farben = { hintergrund: rgb(stil.backgroundColor), schrift: rgb(stil.color), rand: rgb(stil.borderTopColor) }
  probe.remove()
  return farben
}

// Zeigen und Verbergen kommen in der Reihenfolge an, in der sie gerufen wurden;
// sonst bliebe eine Blase stehen, deren Verbergen sie überholt hat.
let kette: Promise<unknown> = Promise.resolve()
function senden(blase: Record<string, unknown> | null): void {
  kette = kette.then(() => nativ.kurzinfo(blase)).catch(() => undefined)
}

let sichtbar = false
let wartet: ReturnType<typeof setTimeout> | undefined

export type Richtung = 'rechts' | 'links'

function zeigen(element: Element, text: string, richtung: Richtung): void {
  const r = element.getBoundingClientRect()
  sichtbar = true
  const x = richtung === 'rechts' ? r.right + ABSTAND : r.left - ABSTAND
  senden({ text, x, y: r.top + r.height / 2, richtung, ...farbenLesen() })
}

export function namenVerbergen(): void {
  clearTimeout(wartet)
  wartet = undefined
  if (!sichtbar) return
  sichtbar = false
  senden(null)
}

function spaeterVerbergen(): void {
  clearTimeout(wartet)
  wartet = sichtbar ? setTimeout(namenVerbergen, NACHLAUF_MS) : undefined
}

/** Handler für ein Symbol der Leiste. */
export function useLeistenname(text: string, richtung: Richtung = 'rechts') {
  return {
    onPointerEnter: (e: PointerEvent<HTMLElement>) => {
      if (e.pointerType !== 'mouse') return
      const element = e.currentTarget
      clearTimeout(wartet)
      // Von Symbol zu Symbol wandert eine offene Blase ohne neue Wartezeit.
      if (sichtbar) zeigen(element, text, richtung)
      else wartet = setTimeout(() => zeigen(element, text, richtung), VERZOEGERUNG_MS)
    },
    onPointerLeave: spaeterVerbergen,
    onPointerDown: namenVerbergen,
    onFocus: (e: FocusEvent<HTMLElement>) => {
      if (e.currentTarget.matches(':focus-visible')) zeigen(e.currentTarget, text, richtung)
    },
    onBlur: namenVerbergen,
  }
}

/** Einmal in der Leiste: verlässt das Fenster den Vordergrund oder geht die Leiste, geht die Blase mit. */
export function useLeistennameAufraeumen(): void {
  useEffect(() => {
    window.addEventListener('blur', namenVerbergen)
    return () => {
      window.removeEventListener('blur', namenVerbergen)
      namenVerbergen()
    }
  }, [])
}
