import { createContext, useContext } from 'react'

/**
 * Kontext für modale Dialoge und Overlays innerhalb begrenzter Bereiche
 * (z. B. dem Seitenpanel des Browsers `.msb-bereich`).
 * Wenn vorhanden, montieren Dialoge und Overlays in diesen Container
 * anstatt an `document.body`, um native WebViews nicht zu überdecken.
 */
export const ModalContainerContext = createContext<HTMLElement | null>(null)

export function useModalContainer(): HTMLElement | null {
  return useContext(ModalContainerContext)
}
