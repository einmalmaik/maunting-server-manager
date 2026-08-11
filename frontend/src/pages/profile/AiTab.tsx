import { Bot } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { AiMemoryManager } from '@/components/ai/AiMemoryManager'
import { AiUsageCard } from '@/components/ai/AiUsageCard'

/**
 * Der persönliche KI-Bereich im Profil — und nur der.
 *
 * Hier standen einmal API-Key-Formulare (mit BYOK entfallen) und bis eben die
 * Skill-Verwaltung. Die gehört nicht hierher: ein Skill gilt entweder panelweit
 * oder in einem Team, nie „für dieses Profil". Er wird deshalb dort gepflegt,
 * wo er gilt — persönliche unter Teams → Persönlich, geteilte beim Team,
 * panelweite in den Einstellungen.
 *
 * Was bleibt, gehört wirklich dem Benutzer: sein Gedächtnis. Niemand sonst
 * sieht es, und es taucht auch unter Teams nicht mehr auf.
 */
export function AiTab() {
  const { t } = useTranslation()

  return (
    <section className="space-y-4" aria-labelledby="ai-profile-title">
      <div className="msm-card p-6">
        <div className="flex items-center gap-2">
          <Bot className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="ai-profile-title" className="font-headline text-lg font-semibold text-on-surface">
            {t('ai.profile.title')}
          </h2>
        </div>
        <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">
          {t('ai.profile.description')}
        </p>
        <p className="mt-3 max-w-3xl text-xs leading-5 text-on-surface-variant">
          {t('ai.profile.skillsMoved')}{' '}
          <Link to="/teams" className="text-primary underline underline-offset-2">
            {t('ai.profile.skillsMovedLink')}
          </Link>
        </p>
      </div>
      {/* Das eigene Kontingent gehört hierher: es ist die Antwort auf „warum
          hat die KI mich abgewiesen?", und die darf nicht nur der Betreiber
          nachschlagen können. */}
      <AiUsageCard />
      <AiMemoryManager />
    </section>
  )
}
