/**
 * Rollenbasierte KI-Kontingente. Die Ansicht ist nur Konfiguration: Das
 * Backend löst mehrere Rollen auf und erzwingt die Werte an den AI-Endpunkten.
 *
 * Es wird bewusst immer nur *eine* Rolle gleichzeitig gezeigt. Vorher standen
 * alle Rollen mit je sechs Zahlenfeldern untereinander — bei einer Handvoll
 * Rollen war die Seite nicht mehr überschaubar und man verlor beim Scrollen,
 * welches Feld zu welcher Rolle gehört.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, Save } from 'lucide-react'

import { api } from '@/api/client'
import { AiMemoryManager } from '@/components/ai/AiMemoryManager'
import { AiSkillManager } from '@/components/ai/AiSkillManager'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Dropdown, NumberStepper, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { AiLearningSettings } from './AiLearningSettings'
import { AiProvidersSettings } from './AiProvidersSettings'
import { AiUsageSettings } from './AiUsageSettings'
import { AiWebSearchSettings } from './AiWebSearchSettings'

export interface AiRoleLimits {
  role_id: number
  role_name: string
  /** False heisst: für diese Rolle ist nichts gespeichert (alle Werte null). */
  configured: boolean
  daily_token_limit: number | null
  weekly_token_limit: number | null
  monthly_token_limit: number | null
  requests_per_minute: number | null
  concurrent_operations: number | null
  monthly_cost_limit_cents: number | null
  /**
   * Hoechste erlaubte Denktiefe als Rang: 0 = gar nicht, 1 = minimal … 6 = max.
   * `null` heisst unbegrenzt — dieselbe Bedeutung wie bei den Kontingenten.
   *
   * Ein Rang und kein Wort, weil jedes Modell andere Stufen kennt: gemessen
   * gibt es bei OpenRouter 20 verschiedene Stufenlisten. Gewaehlt wird spaeter
   * aus den echten Stufen des Modells, der Rang vergleicht nur.
   */
  max_reasoning_effort: number | null
  updated_at: string | null
}

/**
 * Die Woerter zu den Raengen — dieselbe Reihenfolge wie
 * `services/ai_reasoning.RANGFOLGE` im Backend. Rang 0 ist „gar nicht" und hat
 * dort kein Wort; hier bekommt es eines, weil es in der Auswahl stehen muss.
 */
const REASONING_RANKS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const

type LimitField = Exclude<
  keyof AiRoleLimits,
  'role_id' | 'role_name' | 'configured' | 'updated_at'
>

const FIELD_DEFINITIONS: Array<{
  key: LimitField
  labelKey: string
  max: number
  step: number
  /**
   * Statt eines Zahlenfelds eine Auswahl mit Woertern. Nur fuer die Denktiefe:
   * „4" sagt niemandem etwas, „hoch" schon. Alle uebrigen Felder sind echte
   * Mengen und bleiben Zahlen.
   */
  ranks?: readonly string[]
}> = [
  { key: 'daily_token_limit', labelKey: 'aiSettings.dailyTokens', max: 1_000_000_000_000, step: 1_000 },
  { key: 'weekly_token_limit', labelKey: 'aiSettings.weeklyTokens', max: 1_000_000_000_000, step: 10_000 },
  { key: 'monthly_token_limit', labelKey: 'aiSettings.monthlyTokens', max: 1_000_000_000_000, step: 10_000 },
  { key: 'requests_per_minute', labelKey: 'aiSettings.requestsPerMinute', max: 10_000, step: 1 },
  { key: 'concurrent_operations', labelKey: 'aiSettings.concurrentOperations', max: 100, step: 1 },
  { key: 'monthly_cost_limit_cents', labelKey: 'aiSettings.monthlyCostCents', max: 1_000_000_000, step: 100 },
  {
    key: 'max_reasoning_effort',
    labelKey: 'aiSettings.maxReasoningEffort',
    max: REASONING_RANKS.length - 1,
    step: 1,
    ranks: REASONING_RANKS,
  },
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
  const canManageSkills = useHasPermission('ai.skills.manage')
  // Eigenes Recht, nicht `panel.settings.read`: wer Verbraeuche sieht, sieht
  // das Nutzungsverhalten fremder Benutzer.
  const canReadUsage = useHasPermission('ai.usage.read.all')
  const [rows, setRows] = useState<AiRoleLimits[]>([])
  const [selectedRoleId, setSelectedRoleId] = useState<number | null>(null)
  const [loading, setLoading] = useState(canRead)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!canRead) {
      setLoading(false)
      return
    }
    let active = true
    api<AiRoleLimits[]>('/ai/settings/role-limits')
      .then((data) => {
        if (!active) return
        const list = Array.isArray(data) ? data : []
        setRows(list)
        // Bevorzugt die erste bereits konfigurierte Rolle: dort gibt es etwas
        // zu sehen. Ist nichts konfiguriert, greift schlicht die erste Rolle.
        setSelectedRoleId((list.find((row) => row.configured) ?? list[0])?.role_id ?? null)
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

  const selected = useMemo(
    () => rows.find((row) => row.role_id === selectedRoleId) ?? null,
    [rows, selectedRoleId],
  )

  /** Ändert genau ein Feld lokal; gespeichert wird anschließend das Vollset. */
  const updateField = (roleId: number, field: LimitField, value: number | null) => {
    setRows((current) => current.map((row) => (
      row.role_id === roleId ? { ...row, [field]: value } : row
    )))
  }

  const save = async (row: AiRoleLimits) => {
    if (!canWrite || saving) return
    setSaving(true)
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
      setSaving(false)
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
      <AiWebSearchSettings canWrite={canWrite} />
      <AiLearningSettings canWrite={canWrite} />

      {/* Panelweite Skills gehören zum Betreiber, nicht ins Profil eines
          Benutzers — und damit neben die Freigabe der KI-gelernten oben.
          Bis eben wurden sie über dasselbe Panel angelegt, das im Profil
          stand; wer dort etwas eintrug, schrieb unbemerkt für alle. */}
      {canManageSkills && <AiSkillManager scope={{ kind: 'panel', canManage: canWrite }} />}

      {/* Panelweites Gedaechtnis gilt fuer **jeden** Benutzer und lief bisher in
          jedem Gespraech mit, ohne dass es irgendwo sichtbar war — erreichbar
          nur ueber die API. Was fuer alle gilt, gehoert dorthin, wo der
          Betreiber es sieht. */}
      <AiMemoryManager scope={{ kind: 'panel', canManage: canWrite }} />

      {/* Der Verbrauch steht direkt vor den Kontingenten: erst sehen, wohin die
          Kosten fliessen, dann entscheiden, wo eine Grenze hingehoert. */}
      {canReadUsage && <AiUsageSettings />}

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

      {selected && (
        <section className="msm-card p-6" aria-labelledby="ai-role-limits-title">
          <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
            <label className="block w-full max-w-sm space-y-1.5">
              <span id="ai-role-limits-title" className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('aiSettings.selectRole')}
              </span>
              <Dropdown
                value={String(selected.role_id)}
                onChange={(value) => setSelectedRoleId(Number(value))}
                options={rows.map((row) => ({
                  value: String(row.role_id),
                  label: row.role_name,
                  hint: row.configured ? t('aiSettings.configured') : t('aiSettings.notConfigured'),
                }))}
                disabled={saving}
                aria-label={t('aiSettings.selectRole')}
              />
            </label>
            {canWrite && (
              <Button
                type="button"
                disabled={saving}
                onClick={() => void save(selected)}
                aria-label={`${t('settings.save')}: ${selected.role_name}`}
              >
                <Save className="h-4 w-4" aria-hidden="true" />
                {saving ? t('common.loading') : t('settings.save')}
              </Button>
            )}
          </div>

          {!selected.configured && (
            <p className="mb-5 rounded-lg border border-outline-variant/40 bg-surface-container-low/45 p-3 text-xs leading-5 text-on-surface-variant">
              {t('aiSettings.notConfiguredHint')}
            </p>
          )}

          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
            {FIELD_DEFINITIONS.map(({ key, labelKey, max, step, ranks }) => {
              const unlimited = selected[key] === null
              const label = t(labelKey)
              const fieldId = `ai-${selected.role_id}-${key}`
              return (
                <div key={key} className="space-y-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
                  <label htmlFor={fieldId} className="block min-h-10 text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {label}
                  </label>
                  {ranks ? (
                    <select
                      id={fieldId}
                      className="msm-input"
                      value={String(selected[key] ?? 0)}
                      disabled={!canWrite || unlimited || saving}
                      onChange={(event) => updateField(selected.role_id, key, Number(event.target.value))}
                      aria-label={`${label}: ${selected.role_name}`}
                    >
                      {ranks.map((rank, rang) => (
                        <option key={rank} value={rang}>
                          {rang === 0
                            ? t('ai.reasoning.off')
                            : t(`ai.reasoning.levels.${rank}`, { defaultValue: rank })}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <NumberStepper
                      id={fieldId}
                      min={0}
                      max={max}
                      step={step}
                      value={selected[key] ?? 0}
                      disabled={!canWrite || unlimited || saving}
                      onValueChange={(raw) => {
                        const parsed = parseLimitValue(raw, max)
                        if (parsed !== null) updateField(selected.role_id, key, parsed)
                      }}
                      aria-label={`${label}: ${selected.role_name}`}
                    />
                  )}
                  <div className="flex min-h-10 items-center justify-between gap-3">
                    <span className="text-xs text-on-surface-variant">{t('aiSettings.unlimited')}</span>
                    <Switch
                      checked={unlimited}
                      disabled={!canWrite || saving}
                      onCheckedChange={(next) => updateField(selected.role_id, key, next ? null : 0)}
                      aria-label={`${t('aiSettings.unlimited')}: ${label}: ${selected.role_name}`}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}
