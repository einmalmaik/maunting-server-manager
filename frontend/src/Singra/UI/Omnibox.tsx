import React, { forwardRef } from 'react'

export interface OmniboxProps extends React.HTMLAttributes<HTMLDivElement> {
  links?: React.ReactNode
  rechts?: React.ReactNode
}

/**
 * Omnibox (Design-DNA Pille für Adress-, Such- und Befehlsleisten).
 *
 * Zentriert ein flexibles Eingabefeld zwischen optionalen linken Icons/Wählern
 * (z. B. Suchwahl, Sicherheitsschloss) und rechten Status-/Aktionselementen
 * (z. B. Sprachsuche, Ladezustand).
 */
export const Omnibox = forwardRef<HTMLDivElement, OmniboxProps>(function Omnibox(
  { links, rechts, children, className = '', ...props },
  ref,
) {
  return (
    <div
      ref={ref}
      className={`msm-omnibox h-9 px-2 min-w-0 flex-1 [@media(pointer:coarse)]:h-11 ${className}`}
      {...props}
    >
      {links && <div className="flex shrink-0 items-center gap-1.5 mr-2">{links}</div>}
      {children}
      {rechts && <div className="flex shrink-0 items-center gap-1.5 ml-2">{rechts}</div>}
    </div>
  )
})
