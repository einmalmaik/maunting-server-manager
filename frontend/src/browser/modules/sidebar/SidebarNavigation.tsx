import React from 'react'
import {
  MessageSquare,
  Sparkles,
  Lock,
  Star,
  Clock,
  Download,
  Settings,
  Link,
  Shield,
  User,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useBrowserStore, type DrawerType } from '../../services/browserStore'

export function SidebarNavigation() {
  const { t } = useTranslation()
  const {
    openDrawer,
    toggleDrawer,
    isCoupled,
    isMessengerEnabled,
    isAiEnabled,
    isVaultEnabled,
    pairedUser,
  } = useBrowserStore()

  const navItems: { id: DrawerType; label: string; icon: React.ReactNode; show: boolean }[] = [
    {
      id: 'messenger',
      label: t('browser.sidebar.messenger'),
      icon: <MessageSquare className="w-5 h-5 text-primary" />,
      show: isCoupled && isMessengerEnabled,
    },
    {
      id: 'ai',
      label: t('browser.sidebar.ai'),
      icon: <Sparkles className="w-5 h-5 text-primary" />,
      show: isCoupled && isAiEnabled,
    },
    {
      id: 'vault',
      label: t('browser.sidebar.vault'),
      icon: <Lock className="w-5 h-5 text-status-success" />,
      show: isCoupled && isVaultEnabled,
    },
    {
      id: 'bookmarks',
      label: t('browser.sidebar.bookmarks'),
      icon: <Star className="w-5 h-5" />,
      show: true,
    },
    {
      id: 'history',
      label: t('browser.sidebar.history'),
      icon: <Clock className="w-5 h-5" />,
      show: true,
    },
    {
      id: 'downloads',
      label: t('browser.sidebar.downloads'),
      icon: <Download className="w-5 h-5" />,
      show: true,
    },
  ]

  return (
    <aside className="w-12 bg-muted/70 backdrop-blur-md border-r border-border flex flex-col items-center justify-between py-2 select-none z-30 shrink-0">
      {/* Obere Nav-Gruppe */}
      <div className="flex flex-col items-center gap-1.5 w-full">
        {/* App Logo */}
        <div className="p-1 mb-1">
          <img
            src="/msp.png"
            alt="MSB"
            className="w-6 h-6 object-contain rounded-full shadow-sm ring-1 ring-primary/30"
          />
        </div>

        {navItems
          .filter((item) => item.show)
          .map((item) => {
            const isActive = openDrawer === item.id

            return (
              <button
                key={item.id}
                onClick={() => toggleDrawer(item.id)}
                className={`relative p-2.5 rounded-xl transition-all ${
                  isActive
                    ? 'bg-primary/20 text-primary shadow-sm'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                }`}
                aria-label={item.label}
              >
                {item.icon}
                {isActive && (
                  <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-primary rounded-r" />
                )}
              </button>
            )
          })}
      </div>

      {/* Untere Gruppe: Einstellungen & Profil/Kopplung */}
      <div className="flex flex-col items-center gap-1.5 w-full">
        {/* Kopplungs-Status */}
        {!isCoupled && (
          <button
            onClick={() => toggleDrawer('settings')}
            className="p-2 rounded-xl text-status-warning hover:bg-muted transition-colors"
            aria-label={t('browser.sidebar.pairWithServer')}
          >
            <Link className="w-4 h-4 animate-pulse" />
          </button>
        )}

        {/* Einstellungen */}
        <button
          onClick={() => toggleDrawer('settings')}
          className={`p-2.5 rounded-xl transition-all ${
            openDrawer === 'settings'
              ? 'bg-primary/20 text-primary shadow-sm'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground'
          }`}
          aria-label={t('browser.sidebar.settings')}
        >
          <Settings className="w-5 h-5" />
        </button>

        {/* Profilbild / Avatar */}
        <div
          onClick={() => toggleDrawer('settings')}
          className="w-8 h-8 rounded-full bg-muted border border-border flex items-center justify-center text-xs font-semibold cursor-pointer hover:border-primary transition-colors overflow-hidden"
          aria-label={isCoupled ? t('browser.sidebar.loggedInAs', { user: pairedUser?.username || t('browser.sidebar.defaultUser') }) : t('browser.sidebar.notCoupled')}
        >
          {pairedUser?.avatarUrl ? (
            <img src={pairedUser.avatarUrl} alt="" className="w-full h-full object-cover" />
          ) : (
            <User className="w-4 h-4 text-muted-foreground" />
          )}
        </div>
      </div>
    </aside>
  )
}
