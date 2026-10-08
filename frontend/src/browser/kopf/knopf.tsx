/**
 * Ein runder Symbolknopf der Navigationsleiste. Die Kurzinfo steht darüber
 * (`lage="oben"`): darunter beginnt die Seite, und eine Webview liegt immer
 * über der Oberfläche.
 */
import { forwardRef, type ReactNode } from 'react'

import { Kurzinfo } from '@/Singra/UI'

/** Am Finger 44 px (Punkt 86); im MSB nach dem Zeiger, nicht nach der Breite (Punkt 127). */
export const KNOPF =
  'relative flex h-8 w-8 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11 shrink-0 items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface disabled:opacity-40 disabled:hover:bg-transparent'

interface KnopfProps {
  name: string
  onClick: () => void
  disabled?: boolean
  aktiv?: boolean
  /** Die Kurzinfo nennt den Namen; `beschriftung` ersetzt ihn für Screenreader (etwa mit Zahl). */
  beschriftung?: string
  seite?: 'mitte' | 'ende'
  /** Oben, solange der Knopf über der Seite steht; in einer Leiste am oberen Rand unten. */
  lage?: 'oben' | 'unten'
  children: ReactNode
}

export const Knopf = forwardRef<HTMLButtonElement, KnopfProps & { 'aria-expanded'?: boolean; 'aria-haspopup'?: 'dialog' }>(function Knopf(
  { name, onClick, disabled, aktiv, beschriftung, seite, lage = 'oben', children, ...rest },
  ref,
) {
  return (
    <Kurzinfo text={name} lage={lage} seite={seite}>
      <button ref={ref} type="button" onClick={onClick} disabled={disabled} aria-label={beschriftung ?? name} aria-pressed={aktiv} className={KNOPF} {...rest}>
        {children}
      </button>
    </Kurzinfo>
  )
})

/** Kleine Zahl oben rechts an einem Symbol. */
export function Zahl({ wert }: { wert: number }) {
  return (
    <span aria-hidden="true" className="absolute -right-0.5 -top-0.5 min-w-[1rem] rounded-full bg-primary px-1 text-center text-label-sm leading-4 text-on-primary ring-2 ring-surface-container">
      {wert > 99 ? '99+' : wert}
    </span>
  )
}
