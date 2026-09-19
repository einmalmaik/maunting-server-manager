/**
 * Hält den Messenger-PIN am angemeldeten Konto und lässt die automatische
 * Sperre mitlaufen.
 *
 * Beides gehört in die Hülle der Anwendung und nicht in die Messenger-Seite:
 * wer einmal entsperrt hat und dann zum KI-Chat wechselt, soll nicht bei der
 * Rückkehr erneut tippen. Hinge der Haken an der Seite, liefe die Frist nur,
 * solange der Messenger offen ist — und ein zugeklappter Laptop mit offenem
 * Dashboard wäre ein entsperrter Messenger.
 *
 * Läuft in der Web-Oberfläche (`Shell`) und in der App (`DesktopApp`).
 */

import { useEffect } from 'react'

import { useAutoSperre } from '@/hooks/useAutoSperre'
import { messengerAutoSperrQuelle, useMessengerSperre } from '@/services/messengerSperre'
import { useAuthStore } from '@/stores/authStore'

export function useMessengerSperreBereitschaft(): void {
  const kontoId = useAuthStore((s) => s.user?.id ?? null)
  const entsperrt = useMessengerSperre((s) => s.entsperrt)

  // Der PIN hängt am Konto. Ein Kontowechsel im selben Browser ist ein anderer
  // PIN, ein anderer Umschlag und ein anderer Stand.
  useEffect(() => {
    if (kontoId === null) return
    void useMessengerSperre.getState().initialisiere()
  }, [kontoId])

  useAutoSperre(messengerAutoSperrQuelle, entsperrt)
}
