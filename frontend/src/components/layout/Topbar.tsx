import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { Globe, Bell, Menu } from 'lucide-react'

export function Topbar() {
  const { t, i18n } = useTranslation()
  const { user } = useAuthStore()

  const toggleLang = () => {
    const next = i18n.language === 'de' ? 'en' : 'de'
    i18n.changeLanguage(next)
  }

  return (
    <header className="msm-topbar h-16 flex items-center justify-between px-margin-mobile md:px-margin-desktop">
      {/* Mobile Brand + Breadcrumbs */}
      <div className="flex items-center gap-3">
        <button className="md:hidden text-on-surface-variant hover:text-primary transition-colors">
          <Menu className="w-5 h-5" />
        </button>
        <div className="md:hidden">
          <span className="font-headline text-headline-md font-bold text-primary">
            MSM
          </span>
        </div>
        <div className="hidden md:flex items-center font-mono-sm text-mono-sm text-on-surface-variant gap-2">
          <span className="text-primary font-medium">{t('panel.title')}</span>
        </div>
      </div>

      {/* Right Actions */}
      <div className="flex items-center gap-4">
        {/* Language Toggle */}
        <button
          onClick={toggleLang}
          className="hidden sm:flex items-center gap-1.5 font-label-md text-xs text-on-surface-variant hover:text-primary transition-colors"
        >
          <Globe className="w-3.5 h-3.5" />
          {i18n.language.toUpperCase()}
        </button>

        {/* Settings */}
        <button className="hidden sm:block text-on-surface-variant hover:text-primary hover:bg-surface-variant/50 p-2 rounded-full transition-colors active:scale-95">
          <span className="sr-only">Settings</span>
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.1a2 2 0 0 1-1-1.72v-.51a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>

        {/* Notifications */}
        <button className="text-on-surface-variant hover:text-primary hover:bg-surface-variant/50 p-2 rounded-full transition-colors active:scale-95 relative">
          <Bell className="w-[18px] h-[18px]" />
          <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-secondary rounded-full" />
        </button>

        {/* User Avatar */}
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-xs font-medium text-primary border border-outline-variant">
            {user?.username.charAt(0).toUpperCase() || '?'}
          </div>
          <div className="hidden sm:block">
            <p className="font-label-md text-sm text-on-surface leading-tight">
              {user?.username}
            </p>
            {user?.is_owner && (
              <span className="msm-badge-info text-[10px] px-1.5 py-0">
                Owner
              </span>
            )}
          </div>
        </div>
      </div>
    </header>
  )
}
