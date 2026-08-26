/**
 * Boot-Sequenz wie beim Start eines Spiels: erst wovon es geschützt wird
 * (DIS), dann von wem es ist (MauntingStudios), dann das Produkt (MSS).
 *
 * Jede Stufe ist ein Bild mit Ein-/Ausblendung; ein Klick oder eine Taste
 * überspringt die ganze Sequenz. Die App lädt währenddessen im Hintergrund
 * weiter.
 *
 * Fehlt eine Bilddatei, läuft die Stufe trotzdem — nur ohne Bild. Vorher
 * wurde sie übersprungen, und weil das Firmenlogo noch nicht im Repo liegt,
 * bekam die mittlere Stufe nie jemand zu sehen. Eine Stufe, die verschwindet,
 * sieht aus wie ein Fehler im Ablauf; eine ohne Bild sagt wenigstens, dass
 * sie fehlt.
 */
import { useEffect, useRef, useState } from 'react'

import disLogo from './assets/dis-logo.png'
import firmenLogo from './assets/firmen-logo.png'
import msmLogo from './assets/msm-logo.png'

interface Stufe {
  bild: string
  alt: string
  untertitel: string
}

const STUFEN: Stufe[] = [
  { bild: disLogo, alt: 'DIS', untertitel: 'Geschützt durch DIS' },
  { bild: firmenLogo, alt: 'MauntingStudios', untertitel: 'Ein Produkt von MauntingStudios' },
  { bild: msmLogo, alt: 'Maunting Server Manager', untertitel: 'Maunting Smart System' },
]

/** Ein Logo soll stehen, nicht durchhuschen. */
const STUFEN_DAUER_MS = 3200

export function Splash({ onFertig }: { onFertig: () => void }) {
  const [stufe, setStufe] = useState(0)
  const [ohneBild, setOhneBild] = useState(false)
  // Der Aufrufer übergibt meist eine frisch gebaute Funktion. Hängt der
  // Stufentakt an ihrer Identität, startet er bei jedem Rendern des
  // Elternteils neu — und während des Starts rendert das oft. Über die
  // Referenz hängt hier nur noch die Stufe.
  const fertigRef = useRef(onFertig)
  fertigRef.current = onFertig

  // Klick oder Taste überspringt alles — niemand muss die Sequenz absitzen.
  useEffect(() => {
    const ueberspringen = () => fertigRef.current()
    window.addEventListener('keydown', ueberspringen)
    return () => window.removeEventListener('keydown', ueberspringen)
  }, [])

  useEffect(() => {
    if (stufe >= STUFEN.length) {
      fertigRef.current()
      return
    }
    setOhneBild(false)
    const timer = setTimeout(() => setStufe((s) => s + 1), STUFEN_DAUER_MS)
    return () => clearTimeout(timer)
  }, [stufe])

  if (stufe >= STUFEN.length) {
    return null
  }
  const aktuelle = STUFEN[stufe]

  return (
    <div
      className="fixed inset-0 z-50 flex cursor-pointer items-center justify-center bg-surface"
      onClick={onFertig}
      role="presentation"
      data-testid="splash"
    >
      {/* key erzwingt einen frischen Animationslauf je Stufe; die Dauer
          kommt aus derselben Konstante wie der Stufenwechsel. */}
      <div
        key={stufe}
        className="mss-splash-stufe flex flex-col items-center gap-5"
        style={{ animationDuration: `${STUFEN_DAUER_MS}ms` }}
      >
        {!ohneBild && (
          // Rund, immer: die Logos sind kreisförmig angelegt, das Quadrat
          // drumherum ist nur die Datei.
          <img
            src={aktuelle.bild}
            alt={aktuelle.alt}
            className="h-40 w-40 rounded-full object-cover"
            draggable={false}
            onError={() => setOhneBild(true)}
          />
        )}
        <p className="text-xs uppercase tracking-widest text-on-surface-variant">
          {aktuelle.untertitel}
        </p>
      </div>
    </div>
  )
}
