import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { Badge } from '@/components/ui/Badge'
import { Globe, Bell } from 'lucide-react'

export function Topbar() {
  const { t, i18n } = useTranslation()
  const { user } = useAuthStore()

  const toggleLang = () => {
    const next = i18n.language === 'de' ? 'en' : 'de'
    i18n.changeLanguage(next)
  }

  return (
    <header className="h-14 border-b border-border bg-card/30 backdrop-blur-md flex items-center justify-between px-6 sticky top-0 z-10">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          {t('panel.title')}
        </h2>
      </div>

      <div className="flex items-center gap-4">
        <button
          onClick={toggleLang}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <Globe className="w-3.5 h-3.5" />
          {i18n.language.toUpperCase()}
        </button>

        <button className="relative text-muted-foreground hover:text-foreground transition-colors">
          <Bell className="w-4 h-4" />
          <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-primary rounded-full" />
        </button>

        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center text-xs font-medium text-primary">
            {user?.username.charAt(0).toUpperCase() || '?'}
          </div>
          <div className="hidden sm:block">
            <p className="text-sm font-medium text-foreground leading-tight">
              {user?.username}
            </p>
            {user?.is_owner && (
              <Badge variant="info" className="text-[10px] px-1.5 py-0">
                Owner
              </Badge>
            )}
          </div>
        </div>
      </div>
    </header>
  )
}
