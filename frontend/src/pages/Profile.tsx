import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { User, KeyRound, Shield, Link2, AlertTriangle, Bot, MonitorSmartphone } from 'lucide-react'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { AccountTab } from './profile/AccountTab'
import { PasswordTab } from './profile/PasswordTab'
import { TwoFactorTab } from './profile/TwoFactorTab'
import { LinkedAccountsTab } from './profile/LinkedAccountsTab'
import { DangerZoneTab } from './profile/DangerZoneTab'
import { AiTab } from './profile/AiTab'
import { DevicesTab } from './profile/DevicesTab'
import { CredentialsTab } from './profile/CredentialsTab'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PageHeader } from '@/Singra/UI/PageHeader'

type TabId = 'account' | 'password' | '2fa' | 'linked' | 'credentials' | 'ai' | 'devices' | 'danger'

const BASE_TABS: TabDef<TabId>[] = [
  { id: 'account', labelKey: 'profile.tabs.account', icon: User },
  { id: 'password', labelKey: 'profile.tabs.password', icon: KeyRound },
  { id: '2fa', labelKey: 'profile.tabs.2fa', icon: Shield },
  { id: 'linked', labelKey: 'profile.tabs.linked', icon: Link2 },
  // Eigener Zugangsdaten-Tresor: jeder Benutzer verwaltet seine eigenen
  // Steam-/GitHub-Zugaenge selbst, ohne Operator-Hilfe.
  { id: 'credentials', labelKey: 'profile.tabs.credentials', icon: KeyRound },
]

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
  const canUseAi = useHasPermission('ai.chat.use')
  const [activeTab, setActiveTab] = useState<TabId>('account')
  const tabs: TabDef<TabId>[] = [
    ...BASE_TABS,
    ...(canUseAi ? [{ id: 'ai' as const, labelKey: 'profile.tabs.ai', icon: Bot }] : []),
    ...(canUseAi ? [{ id: 'devices' as const, labelKey: 'profile.tabs.devices', icon: MonitorSmartphone }] : []),
    { id: 'danger', labelKey: 'profile.tabs.danger', icon: AlertTriangle, variant: 'danger' },
  ]

  return (
    <div className="msm-page">
      <PageHeader eyebrow={t('pageContext.panel', 'Panel')} title={t('profile.title')} description={t('profile.subtitle')} status={<span className="msm-badge-info">{t(`profile.tabs.${activeTab}`)}</span>} />

      <TabBar
        tabs={tabs}
        active={activeTab}
        onChange={setActiveTab}
        ariaLabel={t('profile.title')}
      />

      {activeTab === 'account' && <AccountTab />}
      {activeTab === 'password' && <PasswordTab />}
      {activeTab === '2fa' && <TwoFactorTab />}
      {activeTab === 'linked' && <LinkedAccountsTab />}
      {activeTab === 'credentials' && <CredentialsTab />}
      {activeTab === 'ai' && canUseAi && <AiTab />}
      {activeTab === 'devices' && canUseAi && <DevicesTab />}
      {activeTab === 'danger' && <DangerZoneTab />}
    </div>
  )
}
