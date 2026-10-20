import { useEffect, useState } from 'react'

import { api } from '@/api/client'
import { AiDevicePairingCard } from '@/components/ai/AiDevicePairingCard'
import { E2eeGeraeteCard } from '@/components/social/E2eeGeraeteCard'
import { useHasPermission } from '@/hooks/useHasPermission'

/**
 * Die beiden Gerätekarten: Kopplung und Nachrichtenzugriff.
 *
 * **Eine Zusammensetzung für zwei Orte.** Profil → Geräte und die Sektion
 * „Geräte" unter Sicherheit (MSS und Pondys Browser) zeigen dieselben Karten;
 * bis 10/2026 gab es nur den Profil-Tab, und wer in der App nach seinen
 * Geräten suchte, fand sie nirgends.
 *
 * Die Schranke sitzt an der Karte und nicht am Tab: Kopplung ist eine
 * Smart-System-Sache und bleibt an `ai.chat.use`, die Liste der Geräte mit
 * Nachrichtenzugriff gehört zum Messenger. Solange der Tab selbst an der
 * KI-Berechtigung hing, sah ein Benutzer ohne sie seine eigenen
 * Geräteschlüssel nirgends und konnte keinen davon entfernen.
 */
export function GeraeteKarten({ social }: { social?: boolean } = {}) {
  const canUseAi = useHasPermission('ai.chat.use')
  const [socialEnabled, setSocialEnabled] = useState(social ?? true)

  useEffect(() => {
    // Kennt der Aufrufer die Antwort schon, wird nicht noch einmal gefragt.
    if (social !== undefined) return
    api<{ social_enabled?: boolean }>('/settings/public')
      .then((res) => {
        if (typeof res.social_enabled === 'boolean') setSocialEnabled(res.social_enabled)
      })
      .catch(() => {})
  }, [social])

  return (
    <div className="space-y-6">
      {canUseAi && <AiDevicePairingCard />}
      {socialEnabled && <E2eeGeraeteCard />}
    </div>
  )
}

/** Eigener Tab für Geräteverwaltung im Profil. */
export function DevicesTab() {
  return (
    <section className="space-y-6" aria-labelledby="devices-profile-title">
      <GeraeteKarten />
    </section>
  )
}
