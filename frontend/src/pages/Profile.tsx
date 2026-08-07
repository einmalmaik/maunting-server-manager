import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { User, KeyRound, Shield, Link2, AlertTriangle, Bot } from 'lucide-react'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { AccountTab } from './profile/AccountTab'
import { PasswordTab } from './profile/PasswordTab'
import { TwoFactorTab } from './profile/TwoFactorTab'
import { LinkedAccountsTab } from './profile/LinkedAccountsTab'
import { DangerZoneTab } from './profile/DangerZoneTab'
import { AiCredentialsTab } from './profile/AiCredentialsTab'
import { CredentialsTab } from './profile/CredentialsTab'
import { useHasPermission } from '@/hooks/useHasPermission'

type TabId = 'account' | 'password' | '2fa' | 'linked' | 'credentials' | 'ai' | 'danger'

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
 * Seit dem Refactor nur noch eine duenne Huelle: TabBar oben, Tab-Content unten.
 * Die schwere Logik (Forms, API-Calls) liegt in den einzelnen Tab-Komponenten.
 *
 * Nutzt den gleichen TabBar-Mechanismus wie `/settings`, damit beide Seiten
 * dasselbe Verhalten, Design und i18n-Schema teilen. Aenderungen am TabBar
 * wirken damit automatisch auf beide Seiten.
 */
export function Profile() {
  const { t } = useTranslation()
  const canUseAi = useHasPermission('ai.chat.use')
  const [activeTab, setActiveTab] = useState<TabId>('account')
  const tabs: TabDef<TabId>[] = [
    ...BASE_TABS,
    ...(canUseAi ? [{ id: 'ai' as const, labelKey: 'profile.tabs.ai', icon: Bot }] : []),
    { id: 'danger', labelKey: 'profile.tabs.danger', icon: AlertTriangle, variant: 'danger' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('profile.title')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          {t('profile.subtitle')}
        </p>
      </div>

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
      {activeTab === 'ai' && canUseAi && <AiCredentialsTab />}
      {activeTab === 'danger' && <DangerZoneTab />}
    </div>
  )
}
