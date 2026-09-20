/**
 * Admin-Audit-Protokoll: listet privilegierte Operator-Aktionen aus dem Backend.
 * Sichtbar nur mit system.audit.read (Route + Nav). Keine Secrets in der Anzeige.
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { History, RefreshCw } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { PageHeader } from '@/Singra/UI/PageHeader'
import {
  type AuditLogRow,
  formatAuditTarget,
  formatAuditTime,
  mapAuditApiRows,
  safeAuditDetails,
} from '@/services/auditPresentation'
import { Button } from '@/Singra/UI'

export function AdminAudit() {
  const { t, i18n } = useTranslation()
  const [rows, setRows] = useState<AuditLogRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionFilter, setActionFilter] = useState('')
  const [limit, setLimit] = useState(50)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      params.set('limit', String(Math.min(Math.max(limit, 1), 200)))
      const filter = actionFilter.trim()
      if (filter) params.set('action', filter)
      const data = await api<unknown>(`/admin/audit-logs?${params.toString()}`)
      setRows(mapAuditApiRows(data))
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : t('audit.loadFailed')
      setError(message)
      setRows([])
      toast.error(message)
    } finally {
      setLoading(false)
    }
  }, [actionFilter, limit, t])

  useEffect(() => {
    void load()
  }, [load])

  const locale = i18n.language?.startsWith('de') ? 'de-DE' : 'en-US'

  return (
    <div className="msm-page">
      <PageHeader
        eyebrow={t('pageContext.administration')}
        title={t('audit.title')}
        description={t('audit.subtitle')}
        status={
          <span className="msm-badge-info">
            {rows.length} {t('audit.entries')}
          </span>
        }
        actions={
          <Button variant="secondary"
            type="button"
            className="inline-flex items-center gap-2"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            {t('common.refresh')}
          </Button>
        }
      />

      <div className="msm-card mb-4 flex flex-wrap items-end gap-3 p-4">
        <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-sm text-on-surface-variant">
          {t('audit.filterAction')}
          <input
            className="msm-input"
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            placeholder={t('audit.filterActionPlaceholder')}
            autoComplete="off"
          />
        </label>
        <label className="flex w-28 flex-col gap-1 text-sm text-on-surface-variant">
          {t('audit.limit')}
          <input
            className="msm-input"
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value) || 50)}
          />
        </label>
        <Button
          type="button"
          onClick={() => void load()}
          disabled={loading}
        >
          {t('audit.applyFilter')}
        </Button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-status-destructive/40 bg-status-destructive/10 p-3 text-sm text-status-destructive">
          {error}
        </div>
      )}

      {!loading && !error && rows.length === 0 && (
        <div className="msm-card flex flex-col items-center gap-2 p-10 text-center text-on-surface-variant">
          <History className="h-8 w-8 opacity-60" />
          <p>{t('audit.empty')}</p>
        </div>
      )}

      {rows.length > 0 && (
        <div className="msm-card overflow-x-auto">
          <table className="w-full min-w-[58rem] text-left text-sm">
            <thead className="border-b border-outline-variant bg-surface-container-high text-xs uppercase tracking-wide text-on-surface-variant">
              <tr>
                <th className="px-3 py-2 font-medium">{t('audit.colTime')}</th>
                <th className="px-3 py-2 font-medium">{t('audit.colUser')}</th>
                <th className="px-3 py-2 font-medium">{t('audit.colAction')}</th>
                <th className="px-3 py-2 font-medium">{t('audit.colOrigin')}</th>
                <th className="px-3 py-2 font-medium">{t('audit.colCorrelation')}</th>
                <th className="px-3 py-2 font-medium">{t('audit.colTarget')}</th>
                <th className="px-3 py-2 font-medium">{t('audit.colDetails')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-outline-variant/60 last:border-0">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-on-surface-variant">
                    {formatAuditTime(row.created_at, locale)}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{row.user_id ?? '—'}</td>
                  <td className="px-3 py-2 font-mono text-xs text-on-surface">{row.action}</td>
                  <td className="px-3 py-2 text-xs text-on-surface-variant">
                    {t(`audit.origins.${row.origin}`, { defaultValue: row.origin })}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-on-surface-variant" title={row.correlation_id ?? undefined}>
                    {row.correlation_id ? row.correlation_id.slice(0, 8) : '—'}
                  </td>
                  <td className="px-3 py-2 text-on-surface-variant">{formatAuditTarget(row)}</td>
                  <td className="max-w-md break-all px-3 py-2 font-mono text-xs text-on-surface-variant">
                    {safeAuditDetails(row.details)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {loading && rows.length === 0 && !error && (
        <p className="text-sm text-on-surface-variant">{t('common.loading')}</p>
      )}
    </div>
  )
}
