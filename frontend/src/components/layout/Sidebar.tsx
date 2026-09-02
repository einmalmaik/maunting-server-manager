import { useEffect, useRef, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Logo } from '@/components/Logo'
import { LogOut, Plus, User as UserIcon, X } from 'lucide-react'
import { buildNavigation, type NavGroupName } from './navigation'
import { DesktopAppDownloadBadge } from './DesktopAppDownloadBadge'
import { Avatar, BenachrichtigungsGlocke } from '@/Singra/UI'

interface SidebarProps {
  mobile?: boolean
  onNavigate?: () => void
}

export function Sidebar({ mobile = false, onNavigate }: SidebarProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const userMenuRef = useRef<HTMLDivElement>(null)

  // Hooks duerfen nicht hinter `||`-Short-Circuit verschwinden — daher beide
  // Permissions getrennt aufrufen und erst danach booleisch verknuepfen.
  const hasUsersRead = useHasPermission('users.read')
  const hasUsersManage = useHasPermission('users.manage')
  const canManageUsers = hasUsersRead || hasUsersManage
  const canManageRoles = useHasPermission('roles.manage')
  const canViewAudit = useHasPermission('system.audit.read')
  const canCreateServer = useHasPermission('servers.create')
  const canViewSettings = useHasPermission('panel.settings.read')
  const canManagePanelBackups = useHasPermission('panel.settings.write')
  const canReadPanelDatabase = useHasPermission('panel.database.read')
  const canReadNodes = useHasPermission('nodes.read')
  const canManageNodes = useHasPermission('nodes.manage')
  const canChatWithAi = useHasPermission('ai.chat.use')
  const canManageAiSkills = useHasPermission('ai.skills.manage')
  const canUseSkills = useHasPermission('ai.skills.use')
  const canUseAi = canChatWithAi || canManageAiSkills
  
  const asideRef = useRef<HTMLElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false)
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setUserMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

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

  const [calendarEnabled, setCalendarEnabled] = useState(true)
  const [notesEnabled, setNotesEnabled] = useState(true)

  useEffect(() => {
    api<{ calendar_enabled?: boolean; notes_enabled?: boolean }>('/settings/public')
      .then((res) => {
        if (typeof res.calendar_enabled === 'boolean') {
          setCalendarEnabled(res.calendar_enabled)
        }
        if (typeof res.notes_enabled === 'boolean') {
          setNotesEnabled(res.notes_enabled)
        }
      })
      .catch(() => {})
  }, [])

  const handleLogout = async () => {
    setUserMenuOpen(false)
    if (onNavigate) onNavigate()
    await logout()
    navigate('/login', { replace: true })
  }

  const handleNavigateProfile = () => {
    setUserMenuOpen(false)
    if (onNavigate) onNavigate()
    navigate('/profile')
  }

  const navItems = buildNavigation({
    dashboard: t('nav.dashboard'), calendar: t('nav.calendar', 'Kalender'), notes: t('nav.notes', 'Notizen'), servers: t('nav.servers'), users: t('nav.users'), roles: t('nav.roles'),
    teams: t('nav.teams'),
    audit: t('nav.audit', 'Audit'),
    settings: t('nav.settings'), blueprints: t('nav.blueprints'), panelBackups: t('nav.panelBackups'),
    panelDatabase: t('nav.panelDatabase', 'Panel-Datenbank'), nodes: t('nav.nodes'), docs: t('nav.docs'), ai: t('nav.ai'),
  }, {
    owner: Boolean(user?.is_owner), canManageUsers, canManageRoles, canViewAudit, canViewSettings,
    canManagePanelBackups, canReadPanelDatabase, canViewNodes: canReadNodes || canManageNodes, canUseAi, canUseSkills,
    calendarEnabled, notesEnabled,
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
      className={`msm-sidebar fixed left-0 top-0 z-40 flex flex-col ${mobile ? 'h-[100dvh] w-full !bg-surface-container-low animate-[slideIn_.18s_ease-out]' : 'hidden h-screen w-64 lg:flex'}`}
    >
      {/* Brand */}
      <div className="px-5 pt-5 pb-6 flex items-center gap-3">
        <Logo size="md" />
        <div>
          <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">
            MSM
          </h1>
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

      {/* Desktop App Download Badge */}
      <DesktopAppDownloadBadge />

      {/* Discord-style Footer: User Profile & Notification Bell */}
      <div className="relative mt-auto border-t border-outline-variant/30 bg-surface-container-low/80 p-2">
        <div className="flex items-center justify-between gap-1.5" ref={userMenuRef}>
          {/* User Button */}
          <button
            type="button"
            onClick={() => setUserMenuOpen((open) => !open)}
            aria-expanded={userMenuOpen}
            aria-haspopup="menu"
            aria-label={t('shell.openUserMenu', 'Open user menu')}
            className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg p-1.5 text-left transition-colors hover:bg-surface-variant/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <Avatar
              src={user?.avatar_url}
              name={user?.username}
              size="sm"
            />
            <div className="min-w-0 flex-1">
              <p className="truncate font-label-md text-xs font-semibold text-on-surface">
                {user?.username}
              </p>
              <p className="truncate font-mono-sm text-[11px] text-on-surface-variant">
                {user?.is_owner ? 'Owner' : user?.email}
              </p>
            </div>
          </button>

          {/* User Dropup Menu */}
          {userMenuOpen && (
            <div
              role="menu"
              className="absolute bottom-full left-2 mb-2 w-56 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-high shadow-2xl z-50 animate-[fadeIn_.12s_ease-out]"
            >
              <div className="border-b border-outline-variant/30 p-3 bg-surface-container">
                <div className="flex items-center gap-2.5">
                  <Avatar
                    src={user?.avatar_url}
                    name={user?.username}
                    size="sm"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-label-md text-sm font-semibold text-on-surface">
                      {user?.username}
                    </p>
                    <p className="truncate font-mono-sm text-xs text-on-surface-variant">
                      {user?.email}
                    </p>
                  </div>
                </div>
              </div>

              <div className="py-1">
                <button
                  type="button"
                  onClick={handleNavigateProfile}
                  role="menuitem"
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface-container-highest"
                >
                  <UserIcon className="h-4 w-4 text-on-surface-variant" aria-hidden="true" />
                  {t('profile.title', 'Profil')}
                </button>
              </div>

              <div className="border-t border-outline-variant/30 py-1">
                <button
                  type="button"
                  onClick={handleLogout}
                  role="menuitem"
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-sm text-status-error transition-colors hover:bg-error-container/20"
                >
                  <LogOut className="h-4 w-4" aria-hidden="true" />
                  {t('nav.logout', 'Abmelden')}
                </button>
              </div>
            </div>
          )}

          {/* Notification Bell */}
          <BenachrichtigungsGlocke placement="top" align="right" />
        </div>
      </div>
    </aside>
  )
}
