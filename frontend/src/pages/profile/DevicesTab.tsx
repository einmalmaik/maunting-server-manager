import { useEffect, useState } from 'react'

import { api } from '@/api/client'
import { AiDevicePairingCard } from '@/components/ai/AiDevicePairingCard'
import { E2eeGeraeteCard } from '@/components/social/E2eeGeraeteCard'
import { useHasPermission } from '@/hooks/useHasPermission'

/**
 * Eigener Tab fuer Geraeteverwaltung: Kopplung und Nachrichtenzugriff.
 *
 * Die Schranke sitzt an der Karte und nicht am Tab. Kopplung ist eine
 * Smart-System-Sache und bleibt an `ai.chat.use`; die Liste der Geraete mit
 * Nachrichtenzugriff gehoert zum Messenger. Solange der Tab selbst an der
 * KI-Berechtigung hing, sah ein Benutzer ohne sie seine eigenen
 * Geraeteschluessel nirgends und konnte keinen davon entfernen.
 */
export function DevicesTab() {
  const canUseAi = useHasPermission('ai.chat.use')
  const [socialEnabled, setSocialEnabled] = useState(true)

  useEffect(() => {
    api<{ social_enabled?: boolean }>('/settings/public')
      .then((res) => {
        if (typeof res.social_enabled === 'boolean') setSocialEnabled(res.social_enabled)
      })
      .catch(() => {})
  }, [])

  return (
    <section className="space-y-6" aria-labelledby="devices-profile-title">
      {canUseAi && <AiDevicePairingCard />}
      {socialEnabled && <E2eeGeraeteCard />}
    </section>
  )
}
