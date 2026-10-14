/**
 * Das Symbol einer Website in der Suchseite.
 *
 * Wie auf der Startseite holt Rust das Symbol der Seite selbst und gibt ein PNG
 * zurück — nur ohne den weißen Hintergrund, den eine Kachel auf der Startseite
 * braucht. Hier steht das Symbol auf einer DNA-Fläche; ein weißer Block mit
 * einem kleinen Punkt wäre darin nur ein Fehler. Ohne Symbol zeigt ein runder
 * DNA-Ton den Anfangsbuchstaben der Domain.
 */
import { useEffect, useState } from 'react'

import { nativ } from '../services/nativ'

/** Ein Aufruf je Adresse und Sitzung, auch wenn mehrere Karten sie zeigen. */
const geholt = new Map<string, Promise<string | null>>()

function holen(url: string): Promise<string | null> {
  let laufend = geholt.get(url)
  if (!laufend) {
    laufend = nativ.kachelSymbol(url).catch(() => null)
    geholt.set(url, laufend)
  }
  return laufend
}

/** Kleiner Abstand der Datei zur Karte: ohne ihn klebt die Funktion am Text. */
export function SeitenSymbol({ url, host, className = '' }: { url: string; host: string; className?: string }) {
  const [symbol, setSymbol] = useState<{ url: string; bild: string | null } | null>(null)

  useEffect(() => {
    let aktuell = true
    void holen(url).then((bild) => aktuell && setSymbol({ url, bild }))
    return () => {
      aktuell = false
    }
  }, [url])

  const bild = symbol?.url === url ? symbol.bild : null
  if (!bild) {
    return (
      <span
        className={`flex h-full w-full items-center justify-center bg-primary-container/40 font-semibold uppercase text-primary ${className}`}
        aria-hidden="true"
      >
        {host.charAt(0)}
      </span>
    )
  }
  return <img src={bild} alt="" draggable={false} className={`h-full w-full object-contain ${className}`} />
}
