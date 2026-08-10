import { Bot } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { AiMemoryManager } from '@/components/ai/AiMemoryManager'
import { AiSkillManager } from '@/components/ai/AiSkillManager'
import { useHasPermission } from '@/hooks/useHasPermission'

/**
 * Der persönliche KI-Bereich im Profil.
 *
 * Hier standen bis eben API-Key-Formulare: jeder Benutzer konnte einen eigenen
 * Schlüssel je Anbieter hinterlegen, und die Auflösung nahm ihn **vor** dem des
 * Betreibers. Für ein Panel, das ein Hoster betreibt, ist das der falsche Weg
 * herum — der Kunde zahlt für den Dienst, und ein eigener Schlüssel wäre ein
 * zweiter Abrechnungspfad neben dem kalkulierten.
 *
 * Schlüssel, Modell und Anbieter legt der Betreiber fest. Was hier bleibt, ist
 * das, was wirklich dem Benutzer gehört: sein Gedächtnis.
 */
export function AiTab() {
  const { t } = useTranslation()
  const darfSkills = useHasPermission('ai.skills.use')

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
      </div>
      <AiMemoryManager />
      {darfSkills && <AiSkillManager />}
    </section>
  )
}
