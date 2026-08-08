import { useTranslation } from 'react-i18next'
import { AiAutonomyPanel } from '@/components/ai/AiAutonomyPanel'
import { AiChat } from '@/components/ai/AiChat'
import { AiSkillManager } from '@/components/ai/AiSkillManager'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PageHeader } from '@/Singra/UI/PageHeader'

export function Ai() {
  const { t } = useTranslation()
  const canChat = useHasPermission('ai.chat.use')
  const canManageSkills = useHasPermission('ai.skills.manage')

  return (
    <div className="msm-page">
      <PageHeader eyebrow={t('pageContext.panel')} title={t('ai.chat.title')} description={t('ai.chat.description')} status={<span className="msm-badge-info">{t('ai.chat.status')}</span>} />
      {canChat && <AiChat />}
      {/* Panelweite Freigabe. `serverId={null}` ist die groessere Entscheidung
          und deshalb bewusst getrennt von der Freigabe je Server. */}
      {canChat && <AiAutonomyPanel serverId={null} />}
      {canManageSkills && <AiSkillManager />}
    </div>
  )
}
