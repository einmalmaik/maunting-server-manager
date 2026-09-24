import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { User, Users, KeyRound, Shield, Link2, AlertTriangle, Bot, MonitorSmartphone, Volume2, Lock, Wallet } from 'lucide-react'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { AccountTab } from './profile/AccountTab'
import { SocialTab } from './profile/SocialTab'
import { AudioTab } from './profile/AudioTab'
import { PasswordTab } from './profile/PasswordTab'
import { TwoFactorTab } from './profile/TwoFactorTab'
import { LinkedAccountsTab } from './profile/LinkedAccountsTab'
import { DangerZoneTab } from './profile/DangerZoneTab'
import { AiTab } from './profile/AiTab'
import { DevicesTab } from './profile/DevicesTab'
import { CredentialsTab } from './profile/CredentialsTab'
import { MessengerSicherheitTab } from './profile/MessengerSicherheitTab'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'

type TabId = 'account' | 'social' | 'audio' | 'messenger' | 'password' | '2fa' | 'linked' | 'credentials' | 'ai' | 'devices' | 'danger'


/**
 * Profil-Orchestrator.
 *
 * Seit dem Refactor nur noch eine dünne Hülle: TabBar oben, Tab-Content unten.
 * Die schwere Logik (Forms, API-Calls) liegt in den einzelnen Tab-Komponenten.
 *
 * Nutzt dieselbe Seitenhülle wie `/settings`: `msm-page` als Breitenrahmen und
 * `PageHeader` als Kopf, dazu denselben TabBar-Mechanismus. Damit teilen beide
 * Seiten Verhalten, Design und i18n-Schema, und Änderungen an den zentralen
 * Bausteinen wirken automatisch auf beide.
 */
export function Profile() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const canUseAi = useHasPermission('ai.chat.use')
  const publicSettings = usePublicSettingsStore()
  const initialTab = (searchParams.get('tab') as TabId) || 'account'
  const [activeTab, setActiveTab] = useState<TabId>(initialTab)

  useEffect(() => {
    const tabParam = searchParams.get('tab') as TabId
    if (tabParam && ['account', 'social', 'audio', 'messenger', 'password', '2fa', 'linked', 'credentials', 'ai', 'devices', 'danger'].includes(tabParam)) {
      if ((tabParam === 'social' || tabParam === 'messenger') && !publicSettings.social_enabled) {
        setActiveTab('account')
      } else {
        setActiveTab(tabParam)
      }
    }
  }, [searchParams, publicSettings.social_enabled])

  useEffect(() => {
    if (!publicSettings.social_enabled && (activeTab === 'social' || activeTab === 'messenger')) {
      setActiveTab('account')
    }
  }, [publicSettings.social_enabled, activeTab])

  const tabs: TabDef<TabId>[] = [
    { id: 'account', labelKey: 'profile.tabs.account', icon: User },
    ...(publicSettings.social_enabled
      ? [
          { id: 'social' as const, labelKey: 'profile.tabs.social', icon: Users },
          { id: 'messenger' as const, labelKey: 'profile.tabs.messenger', icon: Lock },
        ]
      : []),
    { id: 'audio', labelKey: 'profile.tabs.audio', icon: Volume2 },
    { id: 'password', labelKey: 'profile.tabs.password', icon: KeyRound },
    { id: '2fa', labelKey: 'profile.tabs.2fa', icon: Shield },
    { id: 'linked', labelKey: 'profile.tabs.linked', icon: Link2 },
    { id: 'credentials', labelKey: 'profile.tabs.credentials', icon: Wallet },
    ...(canUseAi ? [{ id: 'ai' as const, labelKey: 'profile.tabs.ai', icon: Bot }] : []),
    { id: 'devices' as const, labelKey: 'profile.tabs.devices', icon: MonitorSmartphone },
    { id: 'danger', labelKey: 'profile.tabs.danger', icon: AlertTriangle, variant: 'danger' },
  ]

  return (
    <div className="msm-page">
      <PageHeader eyebrow={t('pageContext.panel')} title={t('profile.title')} description={t('profile.subtitle')} status={<span className="msm-badge-info">{t(`profile.tabs.${activeTab}`)}</span>} />

      <TabBar
        tabs={tabs}
        active={activeTab}
        onChange={setActiveTab}
        ariaLabel={t('profile.title')}
      />

      {activeTab === 'account' && <AccountTab />}
      {activeTab === 'social' && publicSettings.social_enabled && <SocialTab />}
      {activeTab === 'audio' && <AudioTab />}
      {activeTab === 'messenger' && publicSettings.social_enabled && <MessengerSicherheitTab />}
      {activeTab === 'password' && <PasswordTab />}
      {activeTab === '2fa' && <TwoFactorTab />}
      {activeTab === 'linked' && <LinkedAccountsTab />}
      {activeTab === 'credentials' && <CredentialsTab />}
      {activeTab === 'ai' && canUseAi && <AiTab />}
      {activeTab === 'devices' && <DevicesTab />}
      {activeTab === 'danger' && <DangerZoneTab />}
    </div>
  )
}
