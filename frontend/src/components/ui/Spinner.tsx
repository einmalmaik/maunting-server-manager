import { cx } from '@/utils/classNames'

export type SpinnerSize = 'sm' | 'md' | 'lg' | 'xl'

/**
 * Der drehende Ring — bis 09/2026 81-mal von Hand gebaut, in 17 Rezepten und
 * sechs Rahmenfarben (`on-primary`, `primary`, `secondary`, `white`,
 * `on-error`, `current`).
 *
 * Die Farbe kommt jetzt aus der Schrift daneben (`border-current`): in einem
 * Knopf dreht sich der Ring in der Farbe der Knopfschrift, ohne dass die
 * Fundstelle sie noch einmal nennen muss. Wo er allein auf einer Fläche steht
 * und trotzdem leuchten soll, sagt das ein `text-primary` am Aufruf.
 */
const GROESSEN: Record<SpinnerSize, string> = {
  sm: 'h-4 w-4 border-2',
  md: 'h-6 w-6 border-2',
  lg: 'h-8 w-8 border-2',
  xl: 'h-10 w-10 border-2',
}

export function Spinner({
  size = 'sm',
  className = '',
}: {
  size?: SpinnerSize
  className?: string
}) {
  return (
    <span
      className={cx(
        'inline-block shrink-0 rounded-full border-current border-t-transparent animate-spin',
        GROESSEN[size],
        className,
      )}
      aria-hidden="true"
    />
  )
}
