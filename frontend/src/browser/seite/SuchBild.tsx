/**
 * Datenschutzkonforme Bild-Komponente für Maunting Search.
 *
 * Lädt Vorschaubilder ausschließlich über den authentifizierten Server-Proxy
 * (`/api/browser/suche/bild?u=...`). Sollte ein Bild fehlen oder nicht ladbar
 * sein, blendet sich das Element sauber aus (oder zeigt den optionalen
 * DNA-Fallback), sodass niemals ein kaputtes Browser-Bild-Symbol erscheint.
 */
import { useState } from 'react'

import { useBildquelle } from '@/components/ai/geo/Regionalbild'

export interface SuchBildProps {
  url?: string | null
  alt?: string
  className?: string
  fallback?: React.ReactNode
}

export function SuchBild({ url, alt = '', className = '', fallback = null }: SuchBildProps) {
  const pfad = url ? `/browser/suche/bild?u=${encodeURIComponent(url)}` : null
  const { quelle, fehlgeschlagen, bildFehler } = useBildquelle(pfad)
  const [geladen, setGeladen] = useState(false)

  if (!url || fehlgeschlagen || !quelle) {
    return fallback ? <>{fallback}</> : null
  }

  return (
    <img
      src={quelle}
      alt={alt}
      loading="lazy"
      onLoad={() => setGeladen(true)}
      onError={bildFehler}
      className={`${className} transition-opacity duration-200 ${geladen ? 'opacity-100' : 'opacity-0'}`}
    />
  )
}
