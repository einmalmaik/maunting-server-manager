import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Archive,
  Clock,
  Terminal,
  Package,
  FolderOpen,
  Settings2,
  Server,
  ChevronLeft,
  ChevronRight,
  ServerCog,
  LogOut,
  Users,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { cn } from '@/lib/utils'
import { useAuth } from '@/hooks/useAuth'
import { hasPermission, UI_PERMISSIONS } from '@/lib/permissions'
import { Separator } from '@/components/ui/separator'
import { Button } from '@/components/ui/button'
import { useUiLanguage } from '@/lib/ui-language'

interface SidebarProps {
  collapsed: boolean
  onToggle: () => void
}

const BRAND_LINKS = [
  { key: 'website', labelKey: 'website', href: 'https://mauntingstudios.de' },
  { key: 'passwordManager', labelKey: 'passwordManager', href: 'https://singravault.mauntingstudios.de' },
  { key: 'ai', labelKey: 'ai', href: 'https://singra.mauntingstudios.de' },
] as const

export default function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const { user, logout } = useAuth()
  const { copy } = useUiLanguage()
  const navGroups = [
    {
      key: 'overview',
      label: copy.navSections.overview,
      items: [
        { icon: LayoutDashboard, label: copy.nav.dashboard, to: '/dashboard', permission: UI_PERMISSIONS.dashboardView },
        { icon: Server, label: copy.nav.servers, to: '/servers', permission: UI_PERMISSIONS.serversView },
      ],
    },
    {
      key: 'operations',
      label: copy.navSections.operations,
      items: [
        { icon: Terminal, label: copy.nav.console, to: '/console', permission: UI_PERMISSIONS.consoleView },
        { icon: Package, label: copy.nav.mods, to: '/mods', permission: UI_PERMISSIONS.modsView },
        { icon: Settings2, label: copy.nav.config, to: '/config', permission: UI_PERMISSIONS.filesRead },
        { icon: FolderOpen, label: copy.nav.files, to: '/files', permission: UI_PERMISSIONS.filesRead },
      ],
    },
    {
      key: 'maintenance',
      label: copy.navSections.maintenance,
      items: [
        { icon: Archive, label: copy.nav.backups, to: '/backups', permission: UI_PERMISSIONS.backupsView },
        { icon: Clock, label: copy.nav.autorestart, to: '/autorestart', permission: UI_PERMISSIONS.autorestartView },
      ],
    },
    {
      key: 'administration',
      label: copy.navSections.administration,
      items: [
        { icon: Users, label: copy.nav.users, to: '/users', permission: UI_PERMISSIONS.usersView },
      ],
    },
  ]
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => hasPermission(user, item.permission)),
    }))
    .filter((group) => group.items.length > 0)

  const handleLogout = async () => {
    try {
      await logout()
    } catch (err) {
      console.error('Logout failed:', err)
      toast.error(copy.account.logoutFailed)
    }
  }

  return (
    <aside
      className={cn(
        'flex h-screen flex-col border-r border-border bg-[var(--el-1)] transition-all duration-300 ease-in-out',
        collapsed ? 'w-[58px]' : 'w-[256px]',
      )}
      style={{ flexShrink: 0 }}
    >
      {/* ── Brand ─────────────────────────────────────────────────── */}
      <div className="flex h-[56px] items-center gap-3 px-3 overflow-hidden">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-accent/15 border border-accent/25">
          <ServerCog className="h-4 w-4 text-accent" />
        </div>
        {!collapsed && (
          <span className="font-display font-semibold text-sm uppercase tracking-wider text-foreground/90 whitespace-nowrap">
            {copy.appName}
          </span>
        )}
      </div>

      <Separator />

      {/* ── Navigation ───────────────────────────────────────────── */}
      <nav className="flex-1 overflow-y-auto p-2">
        <div className="space-y-4">
          {navGroups.map((group) => (
            <div key={group.key} className="space-y-1">
              {!collapsed && (
                <div className="px-2.5 pb-1">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70">
                    {group.label}
                  </p>
                </div>
              )}
              <div className="space-y-0.5">
                {group.items.map(({ icon: Icon, label, to }) => (
                  <NavLink
                    key={to}
                    to={to}
                    className={({ isActive }) =>
                      cn(
                        'flex h-9 items-center gap-3 rounded-md px-2.5 text-sm transition-all duration-150',
                        'hover:bg-accent/10 hover:text-foreground',
                        isActive
                          ? 'bg-accent/10 text-primary border-l-2 border-accent pl-[calc(0.625rem-1px)]'
                          : 'text-muted-foreground border-l-2 border-transparent',
                      )
                    }
                    title={collapsed ? label : undefined}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    {!collapsed && <span className="whitespace-nowrap">{label}</span>}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </div>
      </nav>

      <Separator />

      {/* ── User + Logout ─────────────────────────────────────────── */}
      <div className="px-2 pb-2 pt-2">
        {collapsed ? (
          <a
            href="https://mauntingstudios.de"
            target="_blank"
            rel="noreferrer"
            className="flex h-9 items-center justify-center rounded-md border border-accent/20 bg-accent/5 text-[11px] font-semibold uppercase tracking-[0.24em] text-accent/80 transition-colors hover:border-accent/40 hover:bg-accent/10 hover:text-accent"
            title={copy.branding.openExternal(copy.branding.company)}
            aria-label={copy.branding.openExternal(copy.branding.company)}
          >
            MS
          </a>
        ) : (
          <div className="rounded-xl border border-accent/20 bg-accent/5 p-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent/80">
              {copy.branding.byline}
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {BRAND_LINKS.map((link) => {
                const label = copy.branding[link.labelKey]
                return (
                  <a
                    key={link.key}
                    href={link.href}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-full border border-accent/20 bg-background/70 px-2.5 py-1 text-[11px] text-foreground/75 transition-colors hover:border-accent/40 hover:text-accent"
                    title={copy.branding.openExternal(label)}
                    aria-label={copy.branding.openExternal(label)}
                  >
                    {label}
                  </a>
                )
              })}
            </div>
          </div>
        )}
      </div>

      <Separator />

      <div className="p-2 space-y-1">
        <NavLink
          to="/account"
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-md px-2.5 py-2 transition-colors',
              'hover:bg-accent/10',
              isActive && 'bg-accent/10',
              collapsed && 'justify-center px-0',
            )
          }
          title={collapsed ? (user?.username ?? copy.account.account) : undefined}
        >
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/20 text-xs font-semibold text-accent uppercase">
            {user?.username?.[0] ?? '?'}
          </div>
          {!collapsed && (
            <span className="text-sm text-foreground/70 truncate">{user?.username ?? copy.account.account}</span>
          )}
        </NavLink>

        <button
          onClick={handleLogout}
          className={cn(
            'flex h-9 w-full items-center gap-3 rounded-md px-2.5 text-sm text-muted-foreground transition-colors',
            'hover:bg-destructive/10 hover:text-destructive',
            collapsed && 'justify-center px-0',
          )}
          title={collapsed ? copy.account.signOut : undefined}
          aria-label={copy.account.signOut}
        >
          <LogOut className="h-4 w-4 shrink-0" />
          {!collapsed && <span>{copy.account.signOut}</span>}
        </button>
      </div>

      {/* ── Collapse toggle ──────────────────────────────────────── */}
      <div className="flex justify-end p-2 border-t border-border">
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggle}
          className="h-7 w-7 text-muted-foreground"
          title={collapsed ? copy.account.expandSidebar : copy.account.collapseSidebar}
          aria-label={collapsed ? copy.account.expandSidebar : copy.account.collapseSidebar}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronLeft className="h-4 w-4" />
          )}
        </Button>
      </div>
    </aside>
  )
}
