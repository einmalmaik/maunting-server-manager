/**
 * Das Symbol einer Kachel, von der Website selbst. Rust holt es und gibt ein
 * neu kodiertes PNG als `data:`-Adresse zurück (`kachelsymbol.rs`); die
 * Oberfläche lädt keine Adresse der Seite. Bis es da ist, und wenn die Seite
 * keines hat, steht der Anfangsbuchstabe.
 */
import { useEffect, useState } from 'react'

import { nativ } from '../../services/nativ'

/** Ein Aufruf je Adresse und Sitzung, auch wenn mehrere Kacheln sie zeigen. */
const geholt = new Map<string, Promise<string | null>>()

function holen(url: string): Promise<string | null> {
  let laufend = geholt.get(url)
  if (!laufend) {
    laufend = nativ.kachelSymbol(url).catch(() => null)
    geholt.set(url, laufend)
  }
  return laufend
}

export function KachelSymbol({ url, host }: { url: string; host: string }) {
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
      <span className="text-title-md uppercase" aria-hidden="true">
        {host.charAt(0)}
      </span>
    )
  }
  return <Bild bild={bild} />
}

/**
 * Auf Weiß wie bei Brave: dunkle Logos (GitHub) verschwänden sonst im dunklen
 * Thema. Blendet weich ein, sobald das Bild dekodiert ist.
 */
function Bild({ bild }: { bild: string }) {
  const [da, setDa] = useState(false)
  return (
    <img
      src={bild}
      alt=""
      draggable={false}
      onLoad={() => setDa(true)}
      className={`h-full w-full bg-white object-contain p-2.5 transition-[opacity,transform] duration-300 ease-out motion-reduce:transition-none ${
        da ? 'scale-100 opacity-100' : 'scale-90 opacity-0'
      }`}
    />
  )
}
