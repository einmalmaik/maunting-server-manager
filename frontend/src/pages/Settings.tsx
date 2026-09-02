import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Globe, Mail, Gamepad2, Flame, KeyRound, Shield, Github, Cloud, FileText, LifeBuoy, ShieldAlert, Bot, Plug, Megaphone, CloudCog } from 'lucide-react'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { api } from '@/api/client'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { GeneralTab } from './settings/GeneralTab'
import { EmailTab } from './settings/EmailTab'
import { SteamTab } from './settings/SteamTab'
import { CurseForgeTab } from './settings/CurseForgeTab'
import { OAuthTab } from './settings/OAuthTab'
import { GitHubTab } from './settings/GitHubTab'
import { BackupTab } from './settings/BackupTab'
import { ImprintTab } from './settings/ImprintTab'
import { SupportWidgetTab } from './settings/SupportWidgetTab'
import { CaptchaTab } from './settings/CaptchaTab'
import { CloudflareTab } from './settings/CloudflareTab'
import { SecurityTab } from './settings/SecurityTab'
import { AiTab } from './settings/AiTab'
import { HosterTab } from './settings/HosterTab'
import { PopupTab } from './settings/PopupTab'
import { VaultSettingsTab } from './settings/VaultSettingsTab'
import { useHasPermission } from '@/hooks/useHasPermission'

type TabId =
  | 'general'
  | 'email'
  | 'steam'
  | 'curseforge'
  | 'github'
  | 'oauth'
  | 'imprint'
  | 'captcha'
  | 'cloudflare'
  | 'supportWidget'
  | 'backup'
  | 'security'
  | 'ai'
  | 'popup'
  | 'hoster'
  | 'vault'

interface PanelSettings {
  vault_enabled: boolean
}

export function Settings() {
  const { t } = useTranslation()
  const canManageBackup = useHasPermission('panel.settings.write')
  // Der Hoster-Tab erscheint nur, wenn die Anbindung ueberhaupt sichtbar ist.
  // Self-Hosted-Betreiber ohne Shop sollen den Bereich gar nicht erst sehen.
  const canReadHoster = useHasPermission('panel.hoster.read')
  const canWriteHoster = useHasPermission('panel.hoster.write')
  const [activeTab, setActiveTab] = useState<TabId>('general')
  const [vaultEnabled, setVaultEnabled] = useState<boolean>(true)

  useEffect(() => {
    let active = true
    api<PanelSettings>('/settings')
      .then((data) => {
        if (active && data && typeof data.vault_enabled === 'boolean') {
          setVaultEnabled(data.vault_enabled)
        }
      })
      .catch(() => {})
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!vaultEnabled && activeTab === 'vault') {
      setActiveTab('general')
    }
  }, [vaultEnabled, activeTab])

  // Backup-Tab: panel.settings.write.
  // Security-Tab: immer sichtbar (Rate-Limits für panel.settings.*;
  // Cluster-Rotation bleibt im Tab selbst auf system.secrets.rotate beschränkt).
  const tabs: TabDef<TabId>[] = [
    { id: 'general', labelKey: 'settings.tabs.general', icon: Globe },
    { id: 'email', labelKey: 'settings.tabs.email', icon: Mail },
    { id: 'steam', labelKey: 'settings.tabs.steam', icon: Gamepad2 },
    { id: 'curseforge', labelKey: 'settings.tabs.curseforge', icon: Flame },
    { id: 'github', labelKey: 'settings.tabs.github', icon: Github },
    { id: 'oauth', labelKey: 'settings.tabs.oauth', icon: KeyRound },
    { id: 'captcha', labelKey: 'settings.tabs.captcha', icon: Shield },
    { id: 'cloudflare', labelKey: 'settings.tabs.cloudflare', icon: CloudCog },
    { id: 'imprint', labelKey: 'settings.tabs.imprint', icon: FileText },
    { id: 'supportWidget', labelKey: 'settings.tabs.supportWidget', icon: LifeBuoy },
    { id: 'popup', labelKey: 'settings.tabs.popup', icon: Megaphone },
    ...(canManageBackup ? [{ id: 'backup' as TabId, labelKey: 'settings.tabs.backup', icon: Cloud }] : []),
    { id: 'security', labelKey: 'settings.tabs.security', icon: ShieldAlert },
    { id: 'ai', labelKey: 'settings.tabs.ai', icon: Bot },
    ...(canReadHoster ? [{ id: 'hoster' as TabId, labelKey: 'settings.tabs.hoster', icon: Plug }] : []),
    ...(vaultEnabled ? [{ id: 'vault' as TabId, labelKey: 'settings.tabs.vault', icon: KeyRound }] : []),
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
      {activeTab === 'curseforge' && <CurseForgeTab />}
      {activeTab === 'github' && <GitHubTab />}
      {activeTab === 'oauth' && <OAuthTab />}
      {activeTab === 'captcha' && <CaptchaTab />}
      {activeTab === 'cloudflare' && <CloudflareTab />}
      {activeTab === 'imprint' && <ImprintTab />}
      {activeTab === 'supportWidget' && <SupportWidgetTab />}
      {activeTab === 'popup' && <PopupTab />}
      {activeTab === 'backup' && <BackupTab />}
      {activeTab === 'security' && <SecurityTab />}
      {activeTab === 'ai' && <AiTab />}
      {activeTab === 'hoster' && canReadHoster && <HosterTab canWrite={canWriteHoster} />}
      {activeTab === 'vault' && vaultEnabled && <VaultSettingsTab />}
    </div>
  )
}
