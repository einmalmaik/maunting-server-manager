import { useTranslation } from 'react-i18next'

import { AiChat } from '@/components/ai/AiChat'
import { useHasPermission } from '@/hooks/useHasPermission'

/**
 * Die KI-Seite ist der Chat — nicht eine Seite *mit* einem Chat.
 *
 * Der eingeklappte Bereich fuer die Skill-Verwaltung ist mit dem Makro-System
 * entfallen: ein Prosa-Skill ist ein Text, kein mehrstufiges Formular, und er
 * wird nicht mehr von Hand gestartet. Verwaltet wird er dort, wo die uebrigen
 * KI-Einstellungen liegen.
 */
export function Ai() {
  const { t } = useTranslation()
  const canChat = useHasPermission('ai.chat.use')

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
    </div>
  )
}
