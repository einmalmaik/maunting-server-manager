/**
 * Die Suche im offenen Chat: Treffer, der aktuelle davon, und das Blättern.
 *
 * Gesucht wird nur im lokalen Klartext dieses Geräts (`verlaufSuche`); der
 * Server sieht keine Suchanfrage. Bis 09/2026 stand das in `Messenger.tsx`.
 */

import { useCallback, useState } from 'react'

import { sucheImChat, type Treffer } from '@/services/verlaufSuche'

/**
 * @param springeZu Scrollt zur Nachricht und lässt sie aufleuchten. Die Seite
 *   besitzt den Verlauf, deshalb kommt das Springen von dort.
 */
export function useChatSuche(blindMailboxId: string, springeZu: (clientUuid: string) => void) {
  const [offen, setOffen] = useState(false)
  const [treffer, setTreffer] = useState<Treffer[]>([])
  const [index, setIndex] = useState(0)
  const [gesperrt, setGesperrt] = useState(false)

  // Die Suchleiste entprellt über `suchen` in einem Effekt. Hinge es an
  // `springeZu`, das jedes Rendern neu entsteht, suchte sie in einer Schleife.
  const suchen = useCallback(
    (frage: string) => {
      if (!blindMailboxId) return
      if (!frage.trim()) {
        setTreffer([])
        setIndex(0)
        setGesperrt(false)
        return
      }
      void sucheImChat(blindMailboxId, frage).then((ergebnis) => {
        setTreffer(ergebnis.treffer)
        setIndex(0)
        setGesperrt(ergebnis.gesperrt)
        if (ergebnis.treffer[0]?.clientUuid) springeZu(ergebnis.treffer[0].clientUuid)
      })
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [blindMailboxId],
  )

  const blaettere = (richtung: 1 | -1) => {
    if (!treffer.length) return
    const naechster = (index + richtung + treffer.length) % treffer.length
    setIndex(naechster)
    const ziel = treffer[naechster]
    if (ziel.clientUuid) springeZu(ziel.clientUuid)
  }

  const schliesse = () => {
    setOffen(false)
    setTreffer([])
    setIndex(0)
  }

  return { offen, oeffne: () => setOffen(true), schliesse, suchen, blaettere, treffer, index, gesperrt }
}
