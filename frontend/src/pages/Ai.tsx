import { useState } from 'react'
import { ChevronDown, Workflow } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { AiChat } from '@/components/ai/AiChat'
import { AiSkillManager } from '@/components/ai/AiSkillManager'
import { useHasPermission } from '@/hooks/useHasPermission'

/**
 * Die KI-Seite ist der Chat — nicht eine Seite *mit* einem Chat.
 *
 * Alles Weitere haengt als Schalter in der Chat-Kopfzeile. Nur die
 * Skill-Verwaltung bleibt ein eigener, standardmaessig eingeklappter Bereich:
 * sie ist ein Formular mit mehreren Schritten und wuerde in einem Popover
 * unbedienbar.
 */
export function Ai() {
  const { t } = useTranslation()
  const canChat = useHasPermission('ai.chat.use')
  const canManageSkills = useHasPermission('ai.skills.manage')
  const [skillsOpen, setSkillsOpen] = useState(false)

  if (!canChat) {
    return (
      <div className="msm-page">
        <div className="msm-card p-6 text-sm text-on-surface-variant">{t('ai.chat.noPermission')}</div>
      </div>
    )
  }

  return (
    // Volle Hoehe abzueglich der Topbar. `min-h-0` ist hier nicht kosmetisch:
    // ohne das kann ein Flex-Kind nicht kleiner werden als sein Inhalt, und der
    // Verlauf wuerde die Seite statt seines eigenen Bereichs scrollen.
    <div className="flex h-[calc(100dvh-4rem)] min-h-0 flex-col md:h-[calc(100dvh-5rem)]">
      <AiChat />

      {canManageSkills && (
        <div className="shrink-0 border-t border-outline-variant/40">
          <button
            type="button"
            onClick={() => setSkillsOpen((current) => !current)}
            aria-expanded={skillsOpen}
            className="flex w-full items-center gap-2 px-4 py-2 text-xs font-medium text-on-surface-variant transition-colors hover:text-on-surface"
          >
            <Workflow className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t('ai.skills.title')}
            <ChevronDown
              className={`ml-auto h-3.5 w-3.5 transition-transform ${skillsOpen ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
          </button>
          {skillsOpen && (
            <div className="max-h-[60vh] overflow-y-auto border-t border-outline-variant/40 p-4">
              <AiSkillManager />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
