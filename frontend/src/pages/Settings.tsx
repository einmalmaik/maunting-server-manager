import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Globe, Mail, Gamepad2, KeyRound, Shield, Github, Cloud, FileText, LifeBuoy, ShieldAlert, Bot, Plug } from 'lucide-react'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { GeneralTab } from './settings/GeneralTab'
import { EmailTab } from './settings/EmailTab'
import { SteamTab } from './settings/SteamTab'
import { OAuthTab } from './settings/OAuthTab'
import { GitHubTab } from './settings/GitHubTab'
import { BackupTab } from './settings/BackupTab'
import { ImprintTab } from './settings/ImprintTab'
import { SupportWidgetTab } from './settings/SupportWidgetTab'
import { CaptchaTab } from './settings/CaptchaTab'
import { SecurityTab } from './settings/SecurityTab'
import { AiTab } from './settings/AiTab'
import { HosterTab } from './settings/HosterTab'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PageHeader } from '@/Singra/UI/PageHeader'

type TabId =
  | 'general'
  | 'email'
  | 'steam'
  | 'github'
  | 'oauth'
  | 'imprint'
  | 'captcha'
  | 'supportWidget'
  | 'backup'
  | 'security'
  | 'ai'
  | 'hoster'

export function Settings() {
  const { t } = useTranslation()
  const canManageBackup = useHasPermission('panel.settings.write')
  // Der Hoster-Tab erscheint nur, wenn die Anbindung ueberhaupt sichtbar ist.
  // Self-Hosted-Betreiber ohne Shop sollen den Bereich gar nicht erst sehen.
  const canReadHoster = useHasPermission('panel.hoster.read')
  const canWriteHoster = useHasPermission('panel.hoster.write')
  const [activeTab, setActiveTab] = useState<TabId>('general')

  // Backup-Tab: panel.settings.write.
  // Security-Tab: immer sichtbar (Rate-Limits für panel.settings.*;
  // Cluster-Rotation bleibt im Tab selbst auf system.secrets.rotate beschränkt).
  const tabs: TabDef<TabId>[] = [
    { id: 'general', labelKey: 'settings.tabs.general', icon: Globe },
    { id: 'email', labelKey: 'settings.tabs.email', icon: Mail },
    { id: 'steam', labelKey: 'settings.tabs.steam', icon: Gamepad2 },
    { id: 'github', labelKey: 'settings.tabs.github', icon: Github },
    { id: 'oauth', labelKey: 'settings.tabs.oauth', icon: KeyRound },
    { id: 'captcha', labelKey: 'settings.tabs.captcha', icon: Shield },
    { id: 'imprint', labelKey: 'settings.tabs.imprint', icon: FileText },
    { id: 'supportWidget', labelKey: 'settings.tabs.supportWidget', icon: LifeBuoy },
    ...(canManageBackup ? [{ id: 'backup' as TabId, labelKey: 'settings.tabs.backup', icon: Cloud }] : []),
    { id: 'security', labelKey: 'settings.tabs.security', icon: ShieldAlert },
    { id: 'ai', labelKey: 'settings.tabs.ai', icon: Bot },
    ...(canReadHoster ? [{ id: 'hoster' as TabId, labelKey: 'settings.tabs.hoster', icon: Plug }] : []),
  ]

  return (
    <div className="msm-page">
      <PageHeader eyebrow={t('pageContext.panel', 'Panel')} title={t('settings.title')} description={t('settings.subtitle')} status={<span className="msm-badge-info">{t(`settings.tabs.${activeTab}`)}</span>} />

      <TabBar
        tabs={tabs}
        active={activeTab}
        onChange={setActiveTab}
        ariaLabel={t('settings.title')}
      />

      {activeTab === 'general' && <GeneralTab />}
      {activeTab === 'email' && <EmailTab />}
      {activeTab === 'steam' && <SteamTab />}
      {activeTab === 'github' && <GitHubTab />}
      {activeTab === 'oauth' && <OAuthTab />}
      {activeTab === 'captcha' && <CaptchaTab />}
      {activeTab === 'imprint' && <ImprintTab />}
      {activeTab === 'supportWidget' && <SupportWidgetTab />}
      {activeTab === 'backup' && <BackupTab />}
      {activeTab === 'security' && <SecurityTab />}
      {activeTab === 'ai' && <AiTab />}
      {activeTab === 'hoster' && canReadHoster && <HosterTab canWrite={canWriteHoster} />}
    </div>
  )
}
