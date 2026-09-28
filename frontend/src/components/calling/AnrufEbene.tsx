import { Suspense, lazy, useEffect } from 'react'
import { useCallStore } from '@/stores/useCallStore'

let overlayLaden: Promise<typeof import('./CallOverlay')> | null = null

// Ein gescheiterter Abruf wird nicht gemerkt, der nächste Versuch lädt neu.
function ladeOverlay() {
  overlayLaden ??= import('./CallOverlay').catch((fehler: unknown) => {
    overlayLaden = null
    throw fehler
  })
  return overlayLaden
}

const CallOverlay = lazy(() => ladeOverlay().then((m) => ({ default: m.CallOverlay })))

/**
 * Nimmt Anrufereignisse an und zeigt das Anruffenster, sobald ein Anruf läuft.
 *
 * Das Fenster zieht LiveKit mit und gehört deshalb nicht ins Startbündel.
 * Es wird nach dem ersten Rendern im Hintergrund geholt: ein eingehender
 * Anruf wartet sonst auf den Download, bevor er klingelt, und ein Tab von
 * vor einem Panel-Update fände den alten Chunk nicht mehr.
 *
 * Der Empfang hängt hier und nicht im Fenster: ein Anruf muss auch
 * ankommen, wenn noch nie ein Fenster offen war.
 */
export function AnrufEbene() {
  const state = useCallStore((s) => s.state)

  useEffect(() => {
    ladeOverlay().catch(() => {
      // Kein Netz oder Chunk weg: beim ersten Anruf wird es erneut versucht.
    })
  }, [])

  useEffect(() => {
    const onSyncEvent = (e: Event) => {
      const custom = e as CustomEvent<{ type?: string; [key: string]: unknown }>
      if (custom.detail) {
        useCallStore.getState().handleCallSyncEvent(custom.detail)
      }
    }
    window.addEventListener('msm:sync-event', onSyncEvent)
    return () => window.removeEventListener('msm:sync-event', onSyncEvent)
  }, [])

  if (state === 'idle') return null
  return (
    <Suspense fallback={null}>
      <CallOverlay />
    </Suspense>
  )
}
