/**
 * Rollenbasierte KI-Kontingente. Die Ansicht ist nur Konfiguration: Das
 * Backend löst mehrere Rollen auf und erzwingt die Werte an den AI-Endpunkten.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, Save } from 'lucide-react'

import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { NumberStepper } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { AiProvidersSettings } from './AiProvidersSettings'

export interface AiRoleLimits {
  role_id: number
  role_name: string
  configured: boolean
  daily_token_limit: number | null
  weekly_token_limit: number | null
  monthly_token_limit: number | null
  requests_per_minute: number | null
  concurrent_operations: number | null
  monthly_cost_limit_cents: number | null
  updated_at: string | null
}

type LimitField = Exclude<
  keyof AiRoleLimits,
  'role_id' | 'role_name' | 'configured' | 'updated_at'
>

const FIELD_DEFINITIONS: Array<{
  key: LimitField
  labelKey: string
  max: number
  step: number
}> = [
  { key: 'daily_token_limit', labelKey: 'aiSettings.dailyTokens', max: 1_000_000_000_000, step: 1_000 },
  { key: 'weekly_token_limit', labelKey: 'aiSettings.weeklyTokens', max: 1_000_000_000_000, step: 10_000 },
  { key: 'monthly_token_limit', labelKey: 'aiSettings.monthlyTokens', max: 1_000_000_000_000, step: 10_000 },
  { key: 'requests_per_minute', labelKey: 'aiSettings.requestsPerMinute', max: 10_000, step: 1 },
  { key: 'concurrent_operations', labelKey: 'aiSettings.concurrentOperations', max: 100, step: 1 },
  { key: 'monthly_cost_limit_cents', labelKey: 'aiSettings.monthlyCostCents', max: 1_000_000_000, step: 100 },
]

/** Wandelt Stepper-Text nur dann um, wenn er eine sichere Ganzzahl darstellt. */
function parseLimitValue(raw: string, max: number): number | null {
  if (!/^\d+$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value >= 0 && value <= max ? value : null
}

export function AiTab() {
  const { t } = useTranslation()
  const canRead = useHasPermission('panel.settings.read')
  const canWrite = useHasPermission('panel.settings.write')
  const [rows, setRows] = useState<AiRoleLimits[]>([])
  const [loading, setLoading] = useState(canRead)
  const [savingRoleId, setSavingRoleId] = useState<number | null>(null)

  useEffect(() => {
    if (!canRead) {
      setLoading(false)
      return
    }
    let active = true
    api<AiRoleLimits[]>('/ai/settings/role-limits')
      .then((data) => {
        if (active) setRows(Array.isArray(data) ? data : [])
      })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof Error ? error.message : t('aiSettings.loadFailed'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [canRead, t])

  /** Ändert genau ein Feld lokal; gespeichert wird anschließend das Vollset. */
  const updateField = (roleId: number, field: LimitField, value: number | null) => {
    setRows((current) => current.map((row) => (
      row.role_id === roleId ? { ...row, [field]: value } : row
    )))
  }

  const save = async (row: AiRoleLimits) => {
    if (!canWrite || savingRoleId !== null) return
    setSavingRoleId(row.role_id)
    try {
      const payload = Object.fromEntries(
        FIELD_DEFINITIONS.map(({ key }) => [key, row[key]]),
      )
      const updated = await api<AiRoleLimits>(`/ai/settings/role-limits/${row.role_id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      setRows((current) => current.map((item) => (
        item.role_id === updated.role_id ? updated : item
      )))
      toast.success(t('aiSettings.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : t('aiSettings.saveFailed'))
    } finally {
      setSavingRoleId(null)
    }
  }

  if (!canRead) {
    return <div className="msm-card p-6 text-sm text-on-surface-variant">{t('aiSettings.noPermission')}</div>
  }
  if (loading) {
    return <div className="flex h-64 items-center justify-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
  }

  return (
    <div className="space-y-5">
      <AiProvidersSettings canWrite={canWrite} />

      <div className="msm-card p-6">
        <div className="mb-3 flex items-center gap-2">
          <Bot className="h-5 w-5 text-primary" aria-hidden="true" />
          <h3 className="font-headline text-lg font-semibold text-on-surface">{t('aiSettings.title')}</h3>
        </div>
        <p className="max-w-3xl text-sm text-on-surface-variant">{t('aiSettings.description')}</p>
        <p className="mt-2 max-w-3xl text-xs text-on-surface-variant">{t('aiSettings.ruleHelp')}</p>
      </div>

      {rows.length === 0 && (
        <div className="msm-card p-6 text-sm text-on-surface-variant">{t('aiSettings.noRoles')}</div>
      )}

      {rows.map((row) => (
        <section key={row.role_id} className="msm-card p-6" aria-labelledby={`ai-role-${row.role_id}`}>
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h4 id={`ai-role-${row.role_id}`} className="font-headline text-base font-semibold text-on-surface">{row.role_name}</h4>
              <p className="text-xs text-on-surface-variant">
                {row.configured ? t('aiSettings.configured') : t('aiSettings.safeDefault')}
              </p>
            </div>
            {canWrite && (
              <button
                type="button"
                className="msm-btn-primary inline-flex min-h-10 items-center gap-2 px-4 py-2 text-sm"
                disabled={savingRoleId !== null}
                onClick={() => void save(row)}
                aria-label={`${t('settings.save')}: ${row.role_name}`}
              >
                <Save className="h-4 w-4" aria-hidden="true" />
                {savingRoleId === row.role_id ? t('common.loading') : t('settings.save')}
              </button>
            )}
          </div>

          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
            {FIELD_DEFINITIONS.map(({ key, labelKey, max, step }) => {
              const unlimited = row[key] === null
              const label = t(labelKey)
              return (
                <div key={key} className="space-y-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
                  <label htmlFor={`ai-${row.role_id}-${key}`} className="block min-h-10 text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {label}
                  </label>
                  <NumberStepper
                    id={`ai-${row.role_id}-${key}`}
                    min={0}
                    max={max}
                    step={step}
                    value={row[key] ?? 0}
                    disabled={!canWrite || unlimited || savingRoleId !== null}
                    onValueChange={(raw) => {
                      const parsed = parseLimitValue(raw, max)
                      if (parsed !== null) updateField(row.role_id, key, parsed)
                    }}
                    aria-label={`${label}: ${row.role_name}`}
                  />
                  <label className="flex min-h-10 items-center gap-2 text-xs text-on-surface-variant">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-primary"
                      checked={unlimited}
                      disabled={!canWrite || savingRoleId !== null}
                      onChange={(event) => updateField(row.role_id, key, event.target.checked ? null : 0)}
                      aria-label={`${t('aiSettings.unlimited')}: ${label}: ${row.role_name}`}
                    />
                    {t('aiSettings.unlimited')}
                  </label>
                </div>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}
