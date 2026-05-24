import { NavLink, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Logo } from '@/components/Logo'
import {
  LayoutDashboard,
  Server,
  Users,
  Shield,
  Settings,
  LogOut,
  Plus,
} from 'lucide-react'

export function Sidebar() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const canManageUsers = useHasPermission('users.read') || useHasPermission('users.manage')
  const canManageRoles = useHasPermission('roles.manage')

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  const navItems = [
    { to: '/', icon: LayoutDashboard, label: t('nav.dashboard') },
    { to: '/servers', icon: Server, label: t('nav.servers') },
    ...((user?.is_owner || canManageUsers) ? [
      { to: '/users', icon: Users, label: t('nav.users') },
    ] : []),
    ...((user?.is_owner || canManageRoles) ? [
      { to: '/roles', icon: Shield, label: t('nav.roles') },
    ] : []),
    { to: '/settings', icon: Settings, label: t('nav.settings') },
  ]

  return (
    <aside className="msm-sidebar hidden md:flex flex-col h-screen fixed left-0 top-0 w-64 z-40">
      {/* Brand */}
      <div className="px-6 pt-6 pb-8 flex items-center gap-3">
        <Logo size="md" />
        <div>
          <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">
            MauntingStudios
          </h1>
          <p className="font-mono-sm text-mono-sm text-on-surface-variant">
            Infrastructure Control
          </p>
        </div>
      </div>

      {/* Create Server Button */}
      {user?.is_owner && (
        <div className="px-4 mb-6">
          <NavLink
            to="/servers"
            className="w-full bg-primary text-on-primary font-label-md text-label-md py-3 rounded-md hover:shadow-primary-glow-hover transition-all flex items-center justify-center gap-2 active:scale-[0.98]"
          >
            <Plus className="w-4 h-4" />
            {t('servers.create', 'Server erstellen')}
          </NavLink>
        </div>
      )}

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              isActive ? 'msm-nav-link-active' : 'msm-nav-link'
            }
          >
            <item.icon className="w-[18px] h-[18px]" />
            <span className="font-label-md text-label-md">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="mt-auto pt-4 border-t border-outline-variant/30 px-2 pb-4">
        <button
          onClick={handleLogout}
          className="msm-nav-link text-on-surface-variant hover:text-error hover:bg-error-container/20"
        >
          <LogOut className="w-[18px] h-[18px]" />
          <span className="font-label-md text-label-md">{t('nav.logout')}</span>
        </button>
      </div>
    </aside>
  )
}
