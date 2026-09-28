import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { User, Users, Shield, Link2, AlertTriangle, Bot, MonitorSmartphone, Volume2 } from 'lucide-react'
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
import { DatenexportKarte } from './profile/DatenexportKarte'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'

type TabId = 'account' | 'security' | 'devices' | 'social' | 'connections' | 'audio' | 'ai' | 'danger'

/**
 * Frühere Reiter, die in einem anderen aufgegangen sind. Alte Links und
 * Lesezeichen (`?tab=2fa`) landen so am richtigen Ort statt auf „Konto".
 */
const FRUEHERE_REITER: Record<string, TabId> = {
  password: 'security',
  '2fa': 'security',
  messenger: 'security',
  linked: 'connections',
  credentials: 'connections',
}

/**
 * Profil-Orchestrator.
 *
 * Nur eine dünne Hülle: TabBar oben, Tab-Content unten. Die schwere Logik
 * (Forms, API-Calls) liegt in den einzelnen Tab-Komponenten; zusammengelegte
 * Reiter zeigen ihre Teile als Karten untereinander.
 *
 * Der aktive Reiter steht nur in `?tab=`. So führen Links, Zurück-Knopf und
 * Weiterleitungen (OAuth-Verknüpfung) an denselben Ort.
 */
export function Profile() {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const canUseAi = useHasPermission('ai.chat.use')
  const publicSettings = usePublicSettingsStore()
  const social = publicSettings.social_enabled

  const tabs: TabDef<TabId>[] = [
    { id: 'account', labelKey: 'profile.tabs.account', icon: User },
    { id: 'security', labelKey: 'profile.tabs.security', icon: Shield },
    { id: 'devices', labelKey: 'profile.tabs.devices', icon: MonitorSmartphone },
    ...(social ? [{ id: 'social' as const, labelKey: 'profile.tabs.social', icon: Users }] : []),
    { id: 'connections', labelKey: 'profile.tabs.connections', icon: Link2 },
    { id: 'audio', labelKey: 'profile.tabs.audio', icon: Volume2 },
    ...(canUseAi ? [{ id: 'ai' as const, labelKey: 'profile.tabs.ai', icon: Bot }] : []),
    { id: 'danger', labelKey: 'profile.tabs.danger', icon: AlertTriangle, variant: 'danger' },
  ]

  const wunsch = searchParams.get('tab') ?? ''
  const aufgeloest = FRUEHERE_REITER[wunsch] ?? wunsch
  const activeTab: TabId = tabs.some((tab) => tab.id === aufgeloest) ? (aufgeloest as TabId) : 'account'

  const waehle = (tab: TabId) => {
    setSearchParams({ tab }, { replace: true })
  }

  return (
    <div className="msm-page">
      <PageHeader eyebrow={t('pageContext.panel')} title={t('profile.title')} description={t('profile.subtitle')} status={<span className="msm-badge-info">{t(`profile.tabs.${activeTab}`)}</span>} />

      <TabBar
        tabs={tabs}
        active={activeTab}
        onChange={waehle}
        ariaLabel={t('profile.title')}
      />

      {activeTab === 'account' && (
        <div className="space-y-6">
          <AccountTab />
          <DatenexportKarte />
        </div>
      )}
      {activeTab === 'security' && (
        <div className="space-y-6">
          <PasswordTab />
          <TwoFactorTab />
          {social && <MessengerSicherheitTab />}
        </div>
      )}
      {activeTab === 'devices' && <DevicesTab />}
      {activeTab === 'social' && <SocialTab />}
      {activeTab === 'connections' && (
        <div className="space-y-6">
          <LinkedAccountsTab />
          <CredentialsTab />
        </div>
      )}
      {activeTab === 'audio' && <AudioTab />}
      {activeTab === 'ai' && <AiTab />}
      {activeTab === 'danger' && <DangerZoneTab />}
    </div>
  )
}
