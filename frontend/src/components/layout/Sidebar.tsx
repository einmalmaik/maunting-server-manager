import { NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import {
  LayoutDashboard,
  Server,
  Users,
  Settings,
  Shield,
  LogOut,
  HardDrive,
} from 'lucide-react'

export function Sidebar() {
  const { t } = useTranslation()
  const { user, logout } = useAuthStore()

  const navItems = [
    { to: '/', icon: LayoutDashboard, label: t('nav.dashboard') },
    { to: '/servers', icon: Server, label: t('nav.servers') },
    { to: '/backups', icon: HardDrive, label: t('nav.backups') },
    ...(user?.is_owner ? [
      { to: '/users', icon: Users, label: t('nav.users') },
    ] : []),
    { to: '/settings', icon: Settings, label: t('nav.settings') },
  ]

  return (
    <aside className="w-64 h-screen border-r border-border bg-card/50 backdrop-blur-md flex flex-col sticky top-0">
      <div className="p-5 border-b border-border">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center">
            <Shield className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-tight text-foreground leading-tight">
              Maunting
            </h1>
            <p className="text-[10px] text-muted-foreground uppercase tracking-wider">
              Server Manager
            </p>
          </div>
        </div>
      </div>

      <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-150 ${
                isActive
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
              }`
            }
          >
            <item.icon className="w-4 h-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="p-3 border-t border-border">
        <button
          onClick={logout}
          className="flex w-full items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-all duration-150"
        >
          <LogOut className="w-4 h-4" />
          {t('nav.logout')}
        </button>
      </div>
    </aside>
  )
}
