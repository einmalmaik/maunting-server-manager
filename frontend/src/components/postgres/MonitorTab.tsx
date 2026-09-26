import { useEffect, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity, Ban, Lock, Octagon, RefreshCw, Wrench } from 'lucide-react'
import { Badge, Button } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { useStudio } from './StudioContext'
import { Empty, ErrorBox, Loading, Section, formatDate, formatNumber, formatRatio, useLoad } from './shared'
import { formatBytes, type StudioLock, type StudioSession } from './studioApi'

const REFRESH_MS = 5000

export function MonitorTab() {
  const { canAdmin } = useStudio()
  return (
    <div className="space-y-4">
      <HealthSection />
      {canAdmin && <SessionsSection />}
      {canAdmin && <LocksSection />}
    </div>
  )
}

function useAutoRefresh(reload: () => void) {
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') reload()
    }, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [reload])
}

function HealthSection() {
  const { t, i18n } = useTranslation()
  const { api, revision, canWrite, runOperation } = useStudio()
  const health = useLoad(() => api.health(), [api, revision])
  const data = health.data
  const deadlocks = Number(data?.database.deadlocks ?? 0)
  return (
    <Section
      title={<span className="inline-flex items-center gap-2"><Activity className="h-4 w-4" />{t('postgresStudio.health.title')}</span>}
      actions={
        <>
          {canWrite && (
            <>
              <Button size="sm" variant="secondary" onClick={() => void runOperation({ op: 'vacuum', analyze: true }, { title: t('postgresStudio.maintenance.vacuumAnalyzeAll') })}>
                <Wrench className="h-3.5 w-3.5" />
                {t('postgresStudio.maintenance.vacuumAnalyzeAll')}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void runOperation({ op: 'analyze' }, { title: t('postgresStudio.maintenance.analyzeAll') })}>
                {t('postgresStudio.maintenance.analyzeAll')}
              </Button>
            </>
          )}
          <Button size="icon" variant="ghost" onClick={() => void health.reload()} aria-label={t('common.refresh')}>
            <RefreshCw className={`h-4 w-4 ${health.loading ? 'animate-spin' : ''}`} />
          </Button>
        </>
      }
    >
      <ErrorBox message={health.error} />
      {!data && health.loading && <Loading />}
      {data && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label={t('postgresStudio.health.cacheHit')} value={formatRatio(data.cache_hit_ratio, i18n.language)} warn={data.cache_hit_ratio != null && data.cache_hit_ratio < 0.95} hint={t('postgresStudio.health.cacheHitHint')} />
            <Metric label={t('postgresStudio.health.indexHit')} value={formatRatio(data.index_hit_ratio, i18n.language)} warn={data.index_hit_ratio != null && data.index_hit_ratio < 0.95} />
            <Metric label={t('postgresStudio.health.size')} value={formatBytes(Number(data.database.size_bytes ?? 0), i18n.language)} />
            <Metric label={t('postgresStudio.health.deadlocks')} value={formatNumber(deadlocks, i18n.language)} warn={deadlocks > 0} />
          </div>
          <div>
            <h4 className="mb-2 text-sm font-semibold text-on-surface">{t('postgresStudio.health.bloat')}</h4>
            <p className="mb-2 text-xs text-on-surface-variant">{t('postgresStudio.health.bloatHint')}</p>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-on-surface-variant">
                  <tr className="border-b border-outline-variant">
                    <th className="py-2 pr-3 font-medium">{t('postgresStudio.health.table')}</th>
                    <th className="py-2 pr-3 font-medium">{t('postgresStudio.health.live')}</th>
                    <th className="py-2 pr-3 font-medium">{t('postgresStudio.health.dead')}</th>
                    <th className="py-2 pr-3 font-medium">{t('postgresStudio.health.lastVacuum')}</th>
                    <th className="py-2 pr-3 font-medium">{t('postgresStudio.health.scans')}</th>
                    <th className="py-2" />
                  </tr>
                </thead>
                <tbody>
                  {data.tables.slice(0, 25).map((row) => {
                    const high = (row.dead_ratio ?? 0) > 0.2 && row.dead > 1000
                    return (
                      <tr key={`${row.schema}.${row.table}`} className="border-b border-outline-variant/50">
                        <td className="py-1.5 pr-3 font-mono text-on-surface">{row.schema}.{row.table}</td>
                        <td className="py-1.5 pr-3">{formatNumber(row.live, i18n.language)}</td>
                        <td className="py-1.5 pr-3">
                          {formatNumber(row.dead, i18n.language)}
                          {row.dead_ratio != null && <Badge className="ml-2" variant={high ? 'warning' : 'default'}>{formatRatio(row.dead_ratio, i18n.language)}</Badge>}
                        </td>
                        <td className="py-1.5 pr-3 text-on-surface-variant">{formatDate(row.last_autovacuum || row.last_vacuum, i18n.language)}</td>
                        <td className="py-1.5 pr-3 text-on-surface-variant">
                          seq {formatNumber(row.seq_scan, i18n.language)} · idx {formatNumber(row.idx_scan, i18n.language)}
                        </td>
                        <td className="py-1.5 text-right">
                          {canWrite && high && (
                            <Button size="sm" variant="ghost" onClick={() => void runOperation({ op: 'vacuum', schema_name: row.schema, table: row.table, analyze: true }, { title: t('postgresStudio.maintenance.vacuumAnalyze') })}>
                              VACUUM
                            </Button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
          <div>
            <h4 className="mb-2 text-sm font-semibold text-on-surface">{t('postgresStudio.health.unusedIndexes')}</h4>
            {data.unused_indexes.length === 0 ? (
              <Empty>{t('postgresStudio.health.noUnusedIndexes')}</Empty>
            ) : (
              <ul className="space-y-1">
                {data.unused_indexes.map((index) => (
                  <li key={`${index.schema}.${index.index}`} className="flex items-center justify-between rounded-md border border-outline-variant px-3 py-1.5 text-xs">
                    <span className="font-mono text-on-surface">
                      {index.schema}.{index.index} <span className="text-on-surface-variant">({index.table})</span>
                    </span>
                    <span className="text-on-surface-variant">{formatBytes(index.size_bytes, i18n.language)}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-xs text-on-surface-variant">{t('postgresStudio.health.unusedHint')}</p>
          </div>
        </div>
      )}
    </Section>
  )
}

function Metric({ label, value, warn, hint }: { label: string; value: string; warn?: boolean; hint?: string }) {
  return (
    <div className={`rounded-lg border p-3 ${warn ? 'border-status-warning/50 bg-status-warning/5' : 'border-outline-variant'}`}>
      <div className="text-xs text-on-surface-variant">{label}</div>
      <div className={`font-headline text-xl ${warn ? 'text-status-warning' : 'text-on-surface'}`}>{value}</div>
      {hint && <div className="mt-1 text-label-sm text-on-surface-variant">{hint}</div>}
    </div>
  )
}

function SessionsSection() {
  const { t, i18n } = useTranslation()
  const { api, dedicated } = useStudio()
  const sessions = useLoad(() => api.sessions(), [api])
  useAutoRefresh(sessions.reload)

  const act = async (session: StudioSession, action: 'cancel' | 'terminate') => {
    if (action === 'terminate') {
      const ok = await confirm({
        title: t('postgresStudio.sessions.terminateTitle'),
        message: t('postgresStudio.sessions.terminateMessage', { pid: session.pid, role: session.role }),
        confirmText: t('postgresStudio.sessions.terminate'),
        danger: true,
      })
      if (!ok) return
    }
    try {
      const result = await api.sessionAction(session.pid, action)
      toast[result.ok ? 'success' : 'warning'](result.ok ? t('postgresStudio.sessions.done') : t('postgresStudio.sessions.notDone'))
      await sessions.reload()
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <Section title={t('postgresStudio.sessions.title')} actions={<span className="text-xs text-on-surface-variant">{dedicated ? t('postgresStudio.sessions.scopeInstance') : t('postgresStudio.sessions.scopeDatabase')}</span>}>
      <ErrorBox message={sessions.error} />
      {sessions.data && sessions.data.length === 0 && <Empty>{t('postgresStudio.sessions.none')}</Empty>}
      {sessions.data && sessions.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs" data-testid="sessions">
            <thead className="text-on-surface-variant">
              <tr className="border-b border-outline-variant">
                <th className="py-2 pr-3 font-medium">PID</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.sessions.who')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.sessions.state')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.sessions.duration')}</th>
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.sessions.query')}</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {sessions.data.map((session) => (
                <tr key={session.pid} className="border-b border-outline-variant/50 align-top">
                  <td className="py-1.5 pr-3 font-mono">{session.pid}</td>
                  <td className="py-1.5 pr-3">
                    <span className="font-mono text-on-surface">{session.role}</span>
                    <span className="block text-on-surface-variant">{session.database}{session.client ? ` · ${session.client}` : ''}</span>
                    {session.application_name && <span className="block text-on-surface-variant">{session.application_name}</span>}
                  </td>
                  <td className="py-1.5 pr-3">
                    <Badge variant={session.state === 'active' ? 'success' : session.state?.startsWith('idle in') ? 'warning' : 'default'}>{session.state || '–'}</Badge>
                    {session.blocked_by.length > 0 && <Badge variant="destructive" className="ml-1">{t('postgresStudio.sessions.blockedBy', { pids: session.blocked_by.join(', ') })}</Badge>}
                    {session.wait_event && <span className="block text-on-surface-variant">{session.wait_event_type}: {session.wait_event}</span>}
                  </td>
                  <td className="py-1.5 pr-3 text-on-surface-variant">
                    {session.query_seconds != null ? `${session.query_seconds.toLocaleString(i18n.language, { maximumFractionDigits: 1 })} s` : '–'}
                  </td>
                  <td className="max-w-md py-1.5 pr-3">
                    <code className="line-clamp-3 break-words font-mono text-label-sm text-on-surface-variant">{session.query}</code>
                  </td>
                  <td className="whitespace-nowrap py-1.5 text-right">
                    <Button size="sm" variant="ghost" onClick={() => void act(session, 'cancel')} aria-label={t('postgresStudio.sessions.cancel')}>
                      <Ban className="h-3.5 w-3.5" />
                      {t('postgresStudio.sessions.cancel')}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void act(session, 'terminate')} aria-label={t('postgresStudio.sessions.terminate')}>
                      <Octagon className="h-3.5 w-3.5 text-status-destructive" />
                      {t('postgresStudio.sessions.terminate')}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}

/** Sperren als Baum: wer wartet auf wen (pg_blocking_pids). */
function LocksSection() {
  const { t } = useTranslation()
  const { api } = useStudio()
  const locks = useLoad(() => api.locks(), [api])
  useAutoRefresh(locks.reload)
  const waiting = (locks.data || []).filter((l) => !l.granted)
  const byPid = new Map<number, StudioLock[]>()
  for (const lock of locks.data || []) byPid.set(lock.pid, [...(byPid.get(lock.pid) || []), lock])
  const blockers = [...new Set(waiting.flatMap((l) => l.blocked_by))].filter((pid) => !waiting.some((w) => w.pid === pid))

  const renderPid = (pid: number, depth: number, seen: Set<number>): ReactNode => {
    const entries = byPid.get(pid) || []
    const first = entries[0]
    const children = waiting.filter((w) => w.blocked_by.includes(pid) && !seen.has(w.pid))
    const nextSeen = new Set(seen).add(pid)
    return (
      <li key={`${pid}-${depth}`}>
        <div className="rounded-md border border-outline-variant p-2 text-xs" style={{ marginLeft: depth * 16 }}>
          <span className="font-mono text-on-surface">PID {pid}</span>
          {first && <span className="ml-2 text-on-surface-variant">{first.role} · {first.state}</span>}
          <span className="ml-2 font-mono text-label-sm text-on-surface-variant">
            {entries.filter((e) => e.relation).map((e) => `${e.mode}${e.granted ? '' : ' ⏳'} ${e.relation}`).join(', ')}
          </span>
          {first?.query && <code className="mt-1 block truncate font-mono text-label-sm text-on-surface-variant">{first.query}</code>}
        </div>
        {children.length > 0 && <ul className="mt-1 space-y-1">{children.map((c) => renderPid(c.pid, depth + 1, nextSeen))}</ul>}
      </li>
    )
  }

  return (
    <Section title={<span className="inline-flex items-center gap-2"><Lock className="h-4 w-4" />{t('postgresStudio.locks.title')}</span>}>
      <ErrorBox message={locks.error} />
      {locks.data && waiting.length === 0 && <Empty>{t('postgresStudio.locks.noWaiting', { count: locks.data.length })}</Empty>}
      {waiting.length > 0 && <ul className="space-y-1" data-testid="lock-tree">{blockers.map((pid) => renderPid(pid, 0, new Set()))}</ul>}
    </Section>
  )
}
