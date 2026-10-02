import React from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive'
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon'

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Unter `md` 44 px hoch (bei `icon` auch breit), darüber die Höhe aus `size`. */
  fingerziel?: boolean
}

const BASIS = 'inline-flex items-center justify-center gap-2 whitespace-nowrap'

const VARIANTEN: Record<ButtonVariant, string> = {
  primary: 'msm-btn-primary',
  secondary: 'msm-btn-secondary',
  ghost: 'msm-btn-ghost',
  destructive: 'msm-btn-destructive',
}

/**
 * Vier Höhen, keine fünfundzwanzig. `md` ist 40 px und damit genauso hoch wie
 * `Input`, `PasswordInput` und der Dropdown-Auslöser: eine Formularzeile
 * fluchtet, statt zu treppen.
 */
const GROESSEN: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-sm rounded-md',
  md: 'h-10 px-4 text-sm rounded-md',
  lg: 'h-12 px-6 text-base rounded-lg',
  icon: 'h-8 w-8 p-0 rounded-md',
}

/**
 * Am Finger ist ein Ziel 44 px groß (AGENTS.md Punkt 86). Ein schlichtes `h-11`
 * im className streitet mit der Höhe aus `size`; die Variante unter `md` steht
 * im CSS hinter ihr und gewinnt. Bis 02.10.2026 trug jede Stelle die Kette
 * selbst, mal ab `sm`, mal ab `md`.
 */
function fingerKlassen(size: ButtonSize): string {
  return size === 'icon' ? 'max-md:h-11 max-md:w-11' : 'max-md:h-11'
}

/**
 * Die Knopfgestalt für alles, was kein `<button>` sein kann — ein `<Link>`, ein
 * `<a>` nach draußen, ein `<label>` um ein Dateifeld. Sie sehen aus wie ein
 * Knopf und sind genauso hoch, ohne einer zu sein.
 *
 * Vorher trug jede dieser Stellen ihr eigenes Rezept; der „Weiter"-Link war
 * 36 px hoch, der Knopf daneben 40.
 */
export function buttonClasses(
  variant: ButtonVariant = 'primary',
  size: ButtonSize = 'md',
  extra = '',
  fingerziel = false,
): string {
  const finger = fingerziel ? ` ${fingerKlassen(size)}` : ''
  return `${BASIS} ${VARIANTEN[variant]} ${GROESSEN[size]}${finger}${extra ? ` ${extra}` : ''}`
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = '', variant = 'primary', size = 'md', fingerziel = false, ...props }, ref) => (
    <button ref={ref} className={buttonClasses(variant, size, className, fingerziel)} {...props} />
  ),
)
Button.displayName = 'Button'
