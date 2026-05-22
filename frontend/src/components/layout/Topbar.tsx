import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { useNavigate } from 'react-router-dom'
import { Logo } from '@/components/Logo'
import { Globe, Bell, Menu, User, LogOut } from 'lucide-react'

export function Topbar() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  const toggleLang = () => {
    const next = i18n.language === 'de' ? 'en' : 'de'
    i18n.changeLanguage(next)
  }

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <header className="msm-topbar h-16 flex items-center justify-between px-margin-mobile md:px-margin-desktop">
      {/* Mobile Brand + Breadcrumbs */}
      <div className="flex items-center gap-3">
        <button className="md:hidden text-on-surface-variant hover:text-primary transition-colors">
          <Menu className="w-5 h-5" />
        </button>
        <div className="md:hidden">
          <Logo size="sm" />
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

        {/* Notifications */}
        <button className="text-on-surface-variant hover:text-primary hover:bg-surface-variant/50 p-2 rounded-full transition-colors active:scale-95 relative">
          <Bell className="w-[18px] h-[18px]" />
          <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-secondary rounded-full" />
        </button>

        {/* User Menu */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="flex items-center gap-2 hover:bg-surface-variant/50 p-1.5 rounded-lg transition-colors"
          >
            <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-xs font-medium text-primary border border-outline-variant">
              {user?.username.charAt(0).toUpperCase() || '?'}
            </div>
            <div className="hidden sm:block text-left">
              <p className="font-label-md text-sm text-on-surface leading-tight">
                {user?.username}
              </p>
              {user?.is_owner && (
                <span className="msm-badge-info text-[10px] px-1.5 py-0">
                  Owner
                </span>
              )}
            </div>
          </button>

          {menuOpen && (
            <div className="absolute right-0 top-full mt-2 w-56 bg-surface-container-high border border-outline-variant rounded-lg shadow-lg z-50 overflow-hidden">
              <div className="p-3 border-b border-outline-variant/30">
                <p className="font-label-md text-sm text-on-surface font-medium truncate">
                  {user?.username}
                </p>
                <p className="font-mono-sm text-mono-sm text-on-surface-variant truncate">
                  {user?.email}
                </p>
              </div>
              <div className="py-1">
                <button
                  onClick={() => { setMenuOpen(false); navigate('/profile') }}
                  className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm text-on-surface hover:bg-surface-container-highest transition-colors"
                >
                  <User className="w-4 h-4 text-on-surface-variant" />
                  {t('profile.title')}
                </button>
              </div>
              <div className="border-t border-outline-variant/30 py-1">
                <button
                  onClick={handleLogout}
                  className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm text-status-error hover:bg-error-container/20 transition-colors"
                >
                  <LogOut className="w-4 h-4" />
                  {t('nav.logout')}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
