import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Globe, Mail, Gamepad2, KeyRound } from 'lucide-react'
import { GeneralTab } from './settings/GeneralTab'
import { EmailTab } from './settings/EmailTab'
import { SteamTab } from './settings/SteamTab'
import { OAuthTab } from './settings/OAuthTab'

type TabId = 'general' | 'email' | 'steam' | 'oauth'

interface TabDef {
  id: TabId
  labelKey: string
  icon: React.ComponentType<{ className?: string }>
}

const TABS: TabDef[] = [
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

      <div className="msm-card p-2 inline-flex flex-wrap gap-1">
        {TABS.map((tab) => {
          const Icon = tab.icon
          const isActive = activeTab === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2 rounded-md text-sm font-medium inline-flex items-center gap-2 transition-colors ${
                isActive
                  ? 'bg-secondary-container text-on-secondary-container'
                  : 'text-on-surface-variant hover:bg-surface-container-high'
              }`}
            >
              <Icon className="w-4 h-4" />
              {t(tab.labelKey)}
            </button>
          )
        })}
      </div>

      {activeTab === 'general' && <GeneralTab />}
      {activeTab === 'email' && <EmailTab />}
      {activeTab === 'steam' && <SteamTab />}
      {activeTab === 'oauth' && <OAuthTab />}
    </div>
  )
}
