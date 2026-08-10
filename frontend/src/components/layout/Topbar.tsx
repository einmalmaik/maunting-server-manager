import { useState, useRef, useEffect, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { useNavigate } from 'react-router-dom'
import { Logo } from '@/components/Logo'
import { Bell, Bot, Mail, Menu, User, LogOut } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { Switch } from '@/components/ui/Switch'

interface TopbarProps {
  onOpenNavigation?: () => void
  menuButtonRef?: RefObject<HTMLButtonElement>
}

export function Topbar({ onOpenNavigation, menuButtonRef }: TopbarProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user, logout, updateUser } = useAuthStore()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  const [notificationsEnabled, setNotificationsEnabled] = useState<boolean>(user?.email_notifications ?? true)
  const [aiNotificationsEnabled, setAiNotificationsEnabled] = useState<boolean>(user?.ai_notifications ?? true)
  const [bellOpen, setBellOpen] = useState(false)
  // Der Punkt an der Glocke sagt nur noch: irgendetwas ist an. Zwei Punkte
  // fuer zwei Schalter waeren an dieser Groesse nicht mehr lesbar.
  const irgendwasAn = notificationsEnabled || aiNotificationsEnabled
  const bellRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (user) {
      setNotificationsEnabled(user.email_notifications)
      setAiNotificationsEnabled(user.ai_notifications !== false)
    }
  }, [user?.email_notifications, user?.ai_notifications])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) {
        setBellOpen(false)
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') { setMenuOpen(false); setBellOpen(false) }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => { document.removeEventListener('mousedown', handleClickOutside); document.removeEventListener('keydown', handleKeyDown) }
  }, [])

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  /**
   * Legt einen der beiden Schalter um.
   *
   * Vorher hing an der Glocke **ein** Schalter mit einem Bestaetigungsdialog,
   * und er steuerte ausschliesslich den E-Mail-Versand. Seit die KI Auftraege im
   * Hintergrund zu Ende fuehrt, gibt es eine zweite Sorte Meldung — und sie an
   * denselben Schalter zu haengen waere falsch: die KI verschickt keine E-Mails,
   * und wer keine Post will, will deswegen nicht auch keinen Hinweis mehr, dass
   * ein laufender Auftrag auf seine Bestaetigung wartet.
   *
   * Der Bestaetigungsdialog faellt dabei weg. Ein Schalter, den man mit einem
   * zweiten Klick zurueckstellt, braucht keine Rueckfrage; sie stand nur im Weg.
   */
  const schalte = async (feld: 'email' | 'ai', naechster: boolean) => {
    if (!user) return
    const vorher = feld === 'email' ? notificationsEnabled : aiNotificationsEnabled
    // Erst anzeigen, dann senden — und bei einem Fehler zurueckdrehen. Ein
    // Schalter, der nach dem Klick eine Sekunde nichts tut, wirkt kaputt.
    if (feld === 'email') setNotificationsEnabled(naechster)
    else setAiNotificationsEnabled(naechster)
    try {
      const param = feld === 'email' ? 'enabled' : 'ai'
      await api(`/auth/me/notifications?${param}=${naechster}`, { method: 'PATCH' })
      updateUser(
        feld === 'email' ? { email_notifications: naechster } : { ai_notifications: naechster },
      )
    } catch {
      if (feld === 'email') setNotificationsEnabled(vorher)
      else setAiNotificationsEnabled(vorher)
      toast.error(t('notifications.updateFailed'))
    }
  }

  return (
    <>
      <header className="msm-topbar h-16 flex items-center justify-between px-margin-mobile md:px-margin-desktop">
        {/* Mobile Brand + Breadcrumbs */}
        <div className="flex items-center gap-3">
          <button
            ref={menuButtonRef}
            type="button"
            onClick={onOpenNavigation}
            className="grid min-h-11 min-w-11 place-items-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-primary lg:hidden"
            aria-label={t('shell.openNavigation', 'Open navigation')}
            aria-haspopup="dialog"
          >
            <Menu className="w-5 h-5" />
          </button>
          <div className="lg:hidden">
            <Logo size="sm" />
          </div>
          <div className="hidden items-center gap-2 font-mono-sm text-mono-sm text-on-surface-variant lg:flex">
            <span className="text-primary font-medium">{t('panel.title')}</span>
          </div>
        </div>

        {/* Right Actions */}
        <div className="flex items-center gap-4">

          {/* Benachrichtigungen: E-Mail und KI, getrennt schaltbar */}
          <div className="relative" ref={bellRef}>
            <button
              onClick={() => setBellOpen((offen) => !offen)}
              aria-expanded={bellOpen}
              aria-haspopup="menu"
              title={irgendwasAn ? t('notifications.activeLabel') : t('notifications.inactiveLabel')}
              aria-label={irgendwasAn ? t('notifications.activeLabel') : t('notifications.inactiveLabel')}
              className="p-2 rounded-full transition-colors active:scale-95 relative hover:bg-surface-variant/50 text-on-surface-variant hover:text-primary"
            >
              <div className="relative inline-flex">
                <Bell className="w-[18px] h-[18px]" />
                <span
                  className={`absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full border-2 border-background ${
                    irgendwasAn ? 'bg-status-success' : 'bg-status-destructive'
                  }`}
                  aria-hidden="true"
                ></span>
              </div>
            </button>

            {bellOpen && (
              <div role="menu" className="absolute right-0 top-full mt-2 w-72 bg-surface-container-high border border-outline-variant rounded-lg shadow-lg z-50 overflow-hidden">
                <div className="p-3 border-b border-outline-variant/30">
                  <p className="font-label-md text-sm text-on-surface font-medium">
                    {t('notifications.title', 'Benachrichtigungen')}
                  </p>
                </div>
                <label className="flex items-start gap-3 px-3 py-2.5 hover:bg-surface-container-highest transition-colors cursor-pointer">
                  <Mail className="mt-0.5 h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-on-surface">
                      {t('notifications.emailLabel', 'E-Mail-Benachrichtigungen')}
                    </span>
                    <span className="block text-xs text-on-surface-variant">
                      {t('notifications.emailHint', 'Anmeldungen, Server-Ereignisse, Updates.')}
                    </span>
                  </span>
                  <Switch
                    checked={notificationsEnabled}
                    onCheckedChange={(wert) => void schalte('email', wert)}
                    aria-label={t('notifications.emailLabel', 'E-Mail-Benachrichtigungen')}
                  />
                </label>
                <label className="flex items-start gap-3 px-3 py-2.5 hover:bg-surface-container-highest transition-colors cursor-pointer">
                  <Bot className="mt-0.5 h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-on-surface">
                      {t('notifications.aiLabel', 'KI-Meldungen im Panel')}
                    </span>
                    <span className="block text-xs text-on-surface-variant">
                      {/* Die KI verschickt keine E-Mails — deshalb ein eigener
                          Schalter und ein eigener Satz dazu. */}
                      {t('notifications.aiHint', 'Hinweis, wenn ein Auftrag fertig ist oder wartet. Keine E-Mails.')}
                    </span>
                  </span>
                  <Switch
                    checked={aiNotificationsEnabled}
                    onCheckedChange={(wert) => void schalte('ai', wert)}
                    aria-label={t('notifications.aiLabel', 'KI-Meldungen im Panel')}
                  />
                </label>
              </div>
            )}
          </div>

          {/* User Menu */}
          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setMenuOpen(!menuOpen)}
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              aria-label={t('shell.openUserMenu', 'Open user menu')}
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
              <div role="menu" className="absolute right-0 top-full mt-2 w-56 bg-surface-container-high border border-outline-variant rounded-lg shadow-lg z-50 overflow-hidden">
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
                    role="menuitem"
                    className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm text-on-surface hover:bg-surface-container-highest transition-colors"
                  >
                    <User className="w-4 h-4 text-on-surface-variant" />
                    {t('profile.title')}
                  </button>
                </div>
                <div className="border-t border-outline-variant/30 py-1">
                  <button
                    onClick={handleLogout}
                    role="menuitem"
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

    </>
  )
}
