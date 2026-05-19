import { useLocation } from 'react-router-dom'
import LanguageSelector from '@/components/LanguageSelector'
import ServerSelector from '@/components/ServerSelector'
import { useUiLanguage } from '@/lib/ui-language'

export default function TopBar() {
  const { pathname } = useLocation()
  const { copy } = useUiLanguage()
  const pageTitles: Record<string, string> = {
    '/dashboard': copy.pageTitles.dashboard,
    '/servers': copy.pageTitles.servers,
    '/backups': copy.pageTitles.backups,
    '/autorestart': copy.pageTitles.autorestart,
    '/console': copy.pageTitles.console,
    '/mods': copy.pageTitles.mods,
    '/config': copy.pageTitles.config,
    '/files': copy.pageTitles.files,
    '/users': copy.pageTitles.users,
    '/account': copy.pageTitles.account,
  }
  const title =
    pageTitles[pathname] ??
    Object.entries(pageTitles)
      .filter(([route]) => pathname === route || pathname.startsWith(route + '/'))
      .sort((a, b) => b[0].length - a[0].length)[0]?.[1] ??
    copy.appName

  return (
    <header className="flex h-[56px] shrink-0 items-center justify-between border-b border-border bg-[var(--el-1)] px-6">
      <h1 className="font-display text-base font-semibold uppercase tracking-widest text-foreground/80">
        {title}
      </h1>
      <div className="flex items-center gap-2">
        <LanguageSelector />
        <ServerSelector />
      </div>
    </header>
  )
}
