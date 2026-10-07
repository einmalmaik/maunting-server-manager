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
import { useBrowserStore, type DrawerType } from '../../services/browserStore'

export function SidebarNavigation() {
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
      label: 'MSM Messenger',
      icon: <MessageSquare className="w-5 h-5 text-primary" />,
      show: isCoupled && isMessengerEnabled,
    },
    {
      id: 'ai',
      label: 'Singra KI-Begleiter',
      icon: <Sparkles className="w-5 h-5 text-primary" />,
      show: isCoupled && isAiEnabled,
    },
    {
      id: 'vault',
      label: 'DIS Passwortmanager',
      icon: <Lock className="w-5 h-5 text-status-success" />,
      show: isCoupled && isVaultEnabled,
    },
    {
      id: 'bookmarks',
      label: 'Lesezeichen',
      icon: <Star className="w-5 h-5" />,
      show: true,
    },
    {
      id: 'history',
      label: 'Verlauf',
      icon: <Clock className="w-5 h-5" />,
      show: true,
    },
    {
      id: 'downloads',
      label: 'Downloads',
      icon: <Download className="w-5 h-5" />,
      show: true,
    },
  ]

  return (
    <aside className="w-12 bg-muted/70 backdrop-blur-md border-r border-border flex flex-col items-center justify-between py-2 select-none z-30 shrink-0">
      {/* Obere Nav-Gruppe */}
      <div className="flex flex-col items-center gap-1.5 w-full">
        {/* App Logo / Shield */}
        <div className="p-2 mb-1 text-primary">
          <Shield className="w-5 h-5" />
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
                title={item.label}
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
            title="Mit MSM-Server koppeln"
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
          title="Browser-Einstellungen"
        >
          <Settings className="w-5 h-5" />
        </button>

        {/* Profilbild / Avatar */}
        <div
          onClick={() => toggleDrawer('settings')}
          className="w-8 h-8 rounded-full bg-muted border border-border flex items-center justify-center text-xs font-semibold cursor-pointer hover:border-primary transition-colors overflow-hidden"
          title={isCoupled ? `Angemeldet als ${pairedUser?.username || 'Benutzer'}` : 'Nicht gekoppelt'}
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
