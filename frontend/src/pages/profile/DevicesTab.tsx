import { AiDevicePairingCard } from '@/components/ai/AiDevicePairingCard'

/**
 * Eigener Tab fuer Geraeteverwaltung und Kopplung (Smart System Desktop/Mobile).
 *
 * Entkoppelt von den reinen KI-Gedaechtnis-Einstellungen: Geraetekopplung ermoeglicht
 * den sicheren Zugriff fuer Desktop- und Mobile-Apps ueber Einmal-Codes und QR-Codes.
 */
export function DevicesTab() {
  return (
    <section className="space-y-6" aria-labelledby="devices-profile-title">
      <AiDevicePairingCard />
    </section>
  )
}
