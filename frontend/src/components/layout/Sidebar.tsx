import { useEffect, useRef, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { useIsOnline } from '@/hooks/useIsOnline'
import { Logo } from '@/components/Logo'
import { LogOut, Plus, User as UserIcon, X } from 'lucide-react'
import { buildNavigation, type NavGroupName } from './navigation'
import { DesktopAppDownloadBadge } from './DesktopAppDownloadBadge'
import { StoryFableBadge } from './StoryFableBadge'
import { BenachrichtigungsGlocke, ProfileDropdown, type ProfileDropdownItem } from '@/Singra/UI'
import { usePresenceAndActivity, type PresenceStatus } from '@/hooks/usePresenceAndActivity'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

interface SidebarProps {
  mobile?: boolean
  onNavigate?: () => void
  presenceStatus?: PresenceStatus
  onPresenceChange?: (status: PresenceStatus) => void
}

export function Sidebar({ mobile = false, onNavigate, presenceStatus: propPresenceStatus, onPresenceChange }: SidebarProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()

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
  const isOnline = useIsOnline()
  const totalMessengerUnread = useMessengerNotificationStore((s) => s.totalUnreadCount)
  
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

  const [calendarEnabled, setCalendarEnabled] = useState(true)
  const [notesEnabled, setNotesEnabled] = useState(true)
  const [socialEnabled, setSocialEnabled] = useState(true)
  const localPresence = usePresenceAndActivity(socialEnabled, !propPresenceStatus && !mobile)
  const presenceStatus = propPresenceStatus ?? localPresence.status
  const handlePresenceChange = onPresenceChange ?? localPresence.changeStatus

  useEffect(() => {
    api<{ calendar_enabled?: boolean; notes_enabled?: boolean; social_enabled?: boolean }>('/settings/public')
      .then((res) => {
        if (typeof res.calendar_enabled === 'boolean') {
          setCalendarEnabled(res.calendar_enabled)
        }
        if (typeof res.notes_enabled === 'boolean') {
          setNotesEnabled(res.notes_enabled)
        }
        if (typeof res.social_enabled === 'boolean') {
          setSocialEnabled(res.social_enabled)
        }
      })
      .catch(() => {})
  }, [])

  const handleLogout = async () => {
    if (onNavigate) onNavigate()
    await logout()
    navigate('/login', { replace: true })
  }

  const handleNavigateProfile = () => {
    if (onNavigate) onNavigate()
    navigate('/profile')
  }

  const profileMenuItems: ProfileDropdownItem[] = [
    {
      key: 'profile',
      label: t('profile.title', 'Profil'),
      icon: <UserIcon className="h-4 w-4" />,
      onClick: handleNavigateProfile,
    },
    {
      key: 'logout',
      label: t('nav.logout', 'Abmelden'),
      icon: <LogOut className="h-4 w-4" />,
      onClick: () => void handleLogout(),
      tone: 'danger',
    },
  ]

  const navItems = buildNavigation({
    dashboard: t('nav.dashboard'), calendar: t('nav.calendar', 'Kalender'), notes: t('nav.notes', 'Notizen'),
    chat: t('nav.chat', 'Chat'),
    servers: t('nav.servers'), users: t('nav.users'), roles: t('nav.roles'),
    teams: t('nav.teams'),
    audit: t('nav.audit', 'Audit'),
    settings: t('nav.settings'), blueprints: t('nav.blueprints'), panelBackups: t('nav.panelBackups'),
    panelDatabase: t('nav.panelDatabase', 'Panel-Datenbank'), nodes: t('nav.nodes'), docs: t('nav.docs'), ai: t('nav.ai'),
  }, {
    owner: Boolean(user?.is_owner), canManageUsers, canManageRoles, canViewAudit, canViewSettings,
    canManagePanelBackups, canReadPanelDatabase, canViewNodes: canReadNodes || canManageNodes, canUseAi, canUseSkills,
    calendarEnabled, notesEnabled, socialEnabled, isOnline,
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
      <div className="px-5 pt-5 pb-6 flex items-center gap-3 shrink-0">
        <Logo size="md" />
        <div>
          <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">
            MSM
          </h1>
        </div>
        {mobile && <button type="button" onClick={onNavigate} className="ml-auto grid min-h-11 min-w-11 place-items-center rounded-lg hover:bg-surface-container-high" aria-label={t('shell.closeNavigation', 'Close navigation')}><X className="h-5 w-5" /></button>}
      </div>

      {/* Create Server Button — nur wenn `servers.create` (Owner-Bypass via Hook) und online. */}
      {canCreateServer && isOnline && (
        <div className="px-4 mb-6 shrink-0">
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
            {items.map((item) => {
              const isChat = item.to === '/chat'
              const showChatBadge = isChat && totalMessengerUnread > 0
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    isActive
                      ? 'msm-nav-link-active'
                      : showChatBadge
                      ? 'msm-nav-link text-primary'
                      : 'msm-nav-link'
                  }
                  end={item.to === '/'}
                >
                  <div className="relative flex items-center justify-center">
                    <item.icon className="w-[18px] h-[18px]" aria-hidden="true" />
                    {showChatBadge && (
                      <span className="absolute -top-1 -right-1 flex h-2 w-2">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
                        <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
                      </span>
                    )}
                  </div>
                  <span className="font-label-md text-label-md flex-1">{item.label}</span>
                  {showChatBadge && (
                    <span className="inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-primary text-on-primary">
                      {totalMessengerUnread > 99 ? '99+' : totalMessengerUnread}
                    </span>
                  )}
                </NavLink>
              )
            })}
          </section>
        ))}
      </nav>

      {/* Produkt-Hinweise: Story Fable ueber dem MSS-Download */}
      <div className="shrink-0">
        <StoryFableBadge />
        <DesktopAppDownloadBadge />
      </div>

      {/* Discord-style Footer: User Profile & Notification Bell */}
      <div className="relative mt-auto border-t border-outline-variant/30 bg-surface-container-low/80 p-2 shrink-0">
        <div className="flex items-center justify-between gap-1.5">
          <ProfileDropdown
            user={user}
            items={profileMenuItems}
            placement="top-left"
            triggerVariant="full"
            status={socialEnabled ? presenceStatus : undefined}
            onStatusChange={socialEnabled ? handlePresenceChange : undefined}
          />

          {/* Notification Bell */}
          <BenachrichtigungsGlocke placement="top" align="sidebar" />
        </div>
      </div>
    </aside>
  )
}
