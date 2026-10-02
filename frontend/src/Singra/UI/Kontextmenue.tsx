/**
 * Kontextmenü am Mauszeiger, etwa für einen Rechtsklick auf eine Datei.
 *
 * Es liegt per Portal an `body`. Vor dem ersten Zeichnen wird es gemessen und
 * so gesetzt, dass es ganz im Fenster bleibt: links vom Zeiger, wenn rechts
 * kein Platz ist, darüber, wenn unten keiner ist. Pfeiltasten wandern durch
 * die Einträge; Escape schließt und gibt den Fokus an den Auslöser zurück.
 * Ein Klick daneben oder Scrollen schließt es.
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { cx } from '@/utils/classNames'
import { useZurueckSchliesst } from './useZurueckSchliesst'
import type { ActionMenuItem } from './ActionMenu'
import { ankerLage, RAND, SCHICHT } from './Ankerlage'

export interface KontextmenueProps {
  /** Zeigerposition beim Öffnen; `null` heißt geschlossen. */
  ort: { x: number; y: number } | null
  items: ActionMenuItem[]
  label: string
  /** Element, das den Fokus nach Escape zurückbekommt. */
  ausloeser?: HTMLElement | null
  onSchliessen: () => void
}

/** Wohin das Menü am Zeiger passt, ohne über den Fensterrand zu ragen. */
export function menueLage(x: number, y: number, breite: number, hoehe: number, fenster: { breite: number; hoehe: number }) {
  return ankerLage({ left: x, right: x, top: y, bottom: y }, breite, hoehe, fenster)
}

export function Kontextmenue({ ort, items, label, ausloeser, onSchliessen }: KontextmenueProps) {
  const menue = useRef<HTMLDivElement>(null)
  const [lage, setLage] = useState<CSSProperties | null>(null)
  const schliessenRef = useRef(onSchliessen)
  schliessenRef.current = onSchliessen
  useZurueckSchliesst(!!ort, () => schliessenRef.current())

  // Vor dem Zeichnen messen, damit das Menü nie einen Frame lang über den Rand ragt.
  useLayoutEffect(() => {
    if (!ort) {
      setLage(null)
      return
    }
    const setzen = () => {
      const box = menue.current?.getBoundingClientRect()
      if (!box) return
      // Höher als das Fenster wird es nicht (`max-h` unten); gerechnet wird mit der Höhe, die bleibt.
      const hoehe = Math.min(box.height, window.innerHeight - 2 * RAND)
      setLage(menueLage(ort.x, ort.y, box.width, hoehe, { breite: window.innerWidth, hoehe: window.innerHeight }))
    }
    setzen()
    window.addEventListener('resize', setzen)
    return () => window.removeEventListener('resize', setzen)
  }, [ort])

  useEffect(() => {
    if (!ort) return
    const fokus = window.requestAnimationFrame(() => menue.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus())
    const daneben = (event: MouseEvent) => {
      if (!menue.current?.contains(event.target as Node)) schliessenRef.current()
    }
    const gescrollt = (event: Event) => {
      if (!menue.current?.contains(event.target as Node)) schliessenRef.current()
    }
    // Escape in der Capture-Phase: vor dem Dialog darunter, der dann
    // `defaultPrevented` sieht und offen bleibt.
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      event.preventDefault()
      schliessenRef.current()
      ausloeser?.focus()
    }
    const taste = (event: KeyboardEvent) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const eintraege = Array.from(menue.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [])
      if (!eintraege.length) return
      event.preventDefault()
      const jetzt = eintraege.indexOf(document.activeElement as HTMLButtonElement)
      const naechster =
        event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? eintraege.length - 1
            : event.key === 'ArrowUp'
              ? (jetzt - 1 + eintraege.length) % eintraege.length
              : (jetzt + 1) % eintraege.length
      eintraege[naechster].focus()
    }
    document.addEventListener('mousedown', daneben)
    window.addEventListener('scroll', gescrollt, true)
    document.addEventListener('keydown', taste)
    document.addEventListener('keydown', escape, true)
    return () => {
      window.cancelAnimationFrame(fokus)
      document.removeEventListener('mousedown', daneben)
      window.removeEventListener('scroll', gescrollt, true)
      document.removeEventListener('keydown', taste)
      document.removeEventListener('keydown', escape, true)
    }
  }, [ort, ausloeser])

  if (!ort) return null
  return createPortal(
    <div
      ref={menue}
      role="menu"
      aria-label={label}
      className="fixed min-w-48 max-h-[calc(100dvh-1rem)] overflow-y-auto rounded-lg border border-outline-variant bg-surface-container-high p-1.5 shadow-panel"
      // Bis gemessen ist, unsichtbar in der Ecke: dort hat es seine volle Breite.
      // Am Zeiger nahe dem Rand würde es schmaler gemessen, als es ist.
      style={{ zIndex: SCHICHT.kontextmenue, ...(lage ?? { left: 0, top: 0, visibility: 'hidden' }) }}
      onClick={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="menuitem"
          disabled={item.disabled}
          onClick={() => {
            item.onSelect()
            onSchliessen()
          }}
          className={cx(
            'flex min-h-11 w-full items-center gap-2 rounded-md px-2.5 text-left text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-9',
            item.separatorBefore && 'mt-1 border-t border-outline-variant',
            item.destructive ? 'text-status-destructive hover:bg-status-destructive/10' : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
          )}
        >
          {item.icon}
          {item.label}
        </button>
      ))}
    </div>,
    document.body,
  )
}
