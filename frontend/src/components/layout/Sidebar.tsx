import { useEffect, useRef } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Logo } from '@/components/Logo'
import { LogOut, Plus, X } from 'lucide-react'
import { buildNavigation, type NavGroupName } from './navigation'

interface SidebarProps {
  mobile?: boolean
  onNavigate?: () => void
}

export function Sidebar({ mobile = false, onNavigate }: SidebarProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  // Hooks duerfen nicht hinter `||`-Short-Circuit verschwinden — daher beide
  // Permissions getrennt aufrufen und erst danach booleisch verknuepfen.
  const hasUsersRead = useHasPermission('users.read')
  const hasUsersManage = useHasPermission('users.manage')
  const canManageUsers = hasUsersRead || hasUsersManage
  const canManageRoles = useHasPermission('roles.manage')
  const canCreateServer = useHasPermission('servers.create')
  const canViewSettings = useHasPermission('panel.settings.read')
  const canManagePanelBackups = useHasPermission('panel.settings.write')
  const canReadPanelDatabase = useHasPermission('panel.database.read')
  const canReadNodes = useHasPermission('nodes.read')
  const canManageNodes = useHasPermission('nodes.manage')
  
  const asideRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!mobile) return
    const aside = asideRef.current
    const firstFocusable = aside?.querySelector<HTMLElement>('button, a[href]')
    firstFocusable?.focus()
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !aside) return
      const focusable = Array.from(aside.querySelectorAll<HTMLElement>('button:not([disabled]), a[href]'))
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', trapFocus)
    return () => document.removeEventListener('keydown', trapFocus)
  }, [mobile])

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  const navItems = buildNavigation({
    dashboard: t('nav.dashboard'), servers: t('nav.servers'), users: t('nav.users'), roles: t('nav.roles'),
    settings: t('nav.settings'), blueprints: t('nav.blueprints'), panelBackups: t('nav.panelBackups'),
    panelDatabase: t('nav.panelDatabase', 'Panel-Datenbank'), nodes: t('nav.nodes'), docs: t('nav.docs'),
  }, {
    owner: Boolean(user?.is_owner), canManageUsers, canManageRoles, canViewSettings,
    canManagePanelBackups, canReadPanelDatabase, canViewNodes: canReadNodes || canManageNodes,
  })
  const groupLabels: Record<NavGroupName, string> = {
    Overview: t('navGroups.overview', 'Overview'), Infrastructure: t('navGroups.infrastructure', 'Infrastructure'),
    Administration: t('navGroups.administration', 'Administration'), Panel: t('navGroups.panel', 'Panel'), Help: t('navGroups.help', 'Help'),
  }
  const groups = (Object.keys(groupLabels) as NavGroupName[]).map(group => ({ group, items: navItems.filter(item => item.group === group) })).filter(group => group.items.length > 0)

  return (
    <aside
      ref={asideRef}
      role={mobile ? 'dialog' : undefined}
      aria-modal={mobile || undefined}
      aria-label={mobile ? t('shell.mainNavigation', 'Main navigation') : undefined}
      className={`msm-sidebar fixed left-0 top-0 z-40 flex flex-col ${mobile ? 'h-[100dvh] w-full !bg-surface-container-low animate-[slideIn_.18s_ease-out]' : 'hidden h-screen w-64 md:flex'}`}
    >
      {/* Brand */}
      <div className="px-5 pt-5 pb-6 flex items-center gap-3">
        <Logo size="md" />
        <div>
          <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">
            MauntingStudios
          </h1>
          <p className="font-mono-sm text-mono-sm text-on-surface-variant">
            Server Manager
          </p>
        </div>
        {mobile && <button type="button" onClick={onNavigate} className="ml-auto grid min-h-11 min-w-11 place-items-center rounded-lg hover:bg-surface-container-high" aria-label={t('shell.closeNavigation', 'Close navigation')}><X className="h-5 w-5" /></button>}
      </div>

      {/* Create Server Button — nur wenn `servers.create` (Owner-Bypass via Hook). */}
      {canCreateServer && (
        <div className="px-4 mb-6">
          <NavLink
            to="/servers"
            onClick={onNavigate}
            className="msm-btn-primary w-full py-3 flex items-center justify-center gap-2"
          >
            <Plus className="w-4 h-4" />
            {t('servers.create', 'Server erstellen')}
          </NavLink>
        </div>
      )}

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 pb-3" aria-label={t('shell.areas', 'Areas')}>
        {groups.map(({ group, items }) => (
          <section key={group} className="mb-3" aria-labelledby={`nav-${group}`}>
            <h2 id={`nav-${group}`} className="px-4 pb-1 pt-2 font-label-md text-[10px] font-semibold uppercase tracking-[.16em] text-on-surface-variant/55">{groupLabels[group]}</h2>
            {items.map((item) => (
              <NavLink key={item.to} to={item.to} onClick={onNavigate} className={({ isActive }) => isActive ? 'msm-nav-link-active' : 'msm-nav-link'} end={item.to === '/'}>
                <item.icon className="w-[18px] h-[18px]" aria-hidden="true" />
                <span className="font-label-md text-label-md">{item.label}</span>
              </NavLink>
            ))}
          </section>
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
