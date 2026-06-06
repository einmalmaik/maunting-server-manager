import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Globe, Mail, Gamepad2, KeyRound } from 'lucide-react'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { GeneralTab } from './settings/GeneralTab'
import { EmailTab } from './settings/EmailTab'
import { SteamTab } from './settings/SteamTab'
import { OAuthTab } from './settings/OAuthTab'

type TabId = 'general' | 'email' | 'steam' | 'oauth'

const TABS: TabDef<TabId>[] = [
  { id: 'general', labelKey: 'settings.tabs.general', icon: Globe },
  { id: 'email', labelKey: 'settings.tabs.email', icon: Mail },
  { id: 'steam', labelKey: 'settings.tabs.steam', icon: Gamepad2 },
  { id: 'oauth', labelKey: 'settings.tabs.oauth', icon: KeyRound },
]

export function Settings() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabId>('general')

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('settings.title')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          {t('settings.subtitle')}
        </p>
      </div>

      <TabBar
        tabs={TABS}
        active={activeTab}
        onChange={setActiveTab}
        ariaLabel={t('settings.title')}
      />

      {activeTab === 'general' && <GeneralTab />}
      {activeTab === 'email' && <EmailTab />}
      {activeTab === 'steam' && <SteamTab />}
      {activeTab === 'oauth' && <OAuthTab />}
    </div>
  )
}
