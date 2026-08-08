import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, ShieldAlert } from 'lucide-react'

import { aiApi, type AiAutonomyGrant } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, NumberStepper } from '@/Singra/UI'
import { useHasPermission } from '@/hooks/useHasPermission'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

const DEFAULT_BUDGET = 10

/**
 * Freigabe des autonomen KI-Modus — panelweit oder fuer genau einen Server.
 *
 * Bewusst sichtbar warnend und mit einem Zwischenschritt: das Einschalten
 * entfernt die Rueckfrage vor Aktionen, die Dateien aendern, Server anlegen und
 * Mods installieren koennen. Was es nicht entfernt, steht im Hinweistext —
 * Berechtigungen gelten unveraendert weiter.
 */
export function AiAutonomyPanel({ serverId = null }: { serverId?: number | null }) {
  const { t } = useTranslation()
  const canUse = useHasPermission('ai.autonomous.use')
  const [grant, setGrant] = useState<AiAutonomyGrant | null>(null)
  const [budget, setBudget] = useState(DEFAULT_BUDGET)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(async () => {
    try {
      const rows = await aiApi.listAutonomyGrants()
      const match = rows.find((row) => row.server_id === serverId) ?? null
      setGrant(match)
      setBudget(match?.max_actions_per_hour ?? DEFAULT_BUDGET)
    } catch {
      // Fehlende Freigaben sind der Normalfall; ein Ladefehler darf die
      // Serverseite nicht mit einer Fehlermeldung ueberziehen.
      setGrant(null)
    } finally {
      setLoaded(true)
    }
  }, [serverId])

  useEffect(() => {
    if (canUse) void load()
  }, [canUse, load])

  if (!canUse || !loaded) return null

  const enabled = Boolean(grant?.enabled)

  const save = async (nextEnabled: boolean, nextBudget: number) => {
    if (nextEnabled && !enabled) {
      const accepted = await confirm({
        title: t('ai.autonomy.confirmTitle'),
        message: t(serverId === null ? 'ai.autonomy.confirmPanel' : 'ai.autonomy.confirmServer'),
        confirmText: t('ai.autonomy.enable'),
        danger: true,
      })
      if (!accepted) return
    }
    setBusy(true)
    try {
      const saved = await aiApi.saveAutonomyGrant({
        server_id: serverId,
        enabled: nextEnabled,
        max_actions_per_hour: nextBudget,
      })
      setGrant(saved)
      setBudget(saved.max_actions_per_hour)
      toast.success(t('ai.autonomy.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.autonomy.error'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="msm-card p-5" aria-labelledby="ai-autonomy-title">
      <div className="flex items-start gap-3">
        <span className="rounded-lg bg-surface-container-highest p-2 text-primary">
          <Bot className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 id="ai-autonomy-title" className="text-sm font-semibold text-on-surface">
            {t('ai.autonomy.title')}
          </h3>
          <p className="mt-1 text-sm leading-6 text-on-surface-variant">
            {t(serverId === null ? 'ai.autonomy.descriptionPanel' : 'ai.autonomy.descriptionServer')}
          </p>
        </div>
      </div>

      <label className="mt-4 flex items-center gap-3">
        <input
          type="checkbox"
          className="h-4 w-4"
          checked={enabled}
          disabled={busy}
          onChange={(event) => void save(event.target.checked, budget)}
          aria-label={t('ai.autonomy.toggle')}
        />
        <span className="text-sm text-on-surface">{t('ai.autonomy.toggle')}</span>
      </label>

      {enabled && (
        <div className="mt-4 space-y-3">
          <label className="block">
            <span className="mb-1 block text-sm text-on-surface">{t('ai.autonomy.budget')}</span>
            <NumberStepper
              value={budget}
              min={0}
              max={1000}
              step={1}
              disabled={busy}
              onValueChange={(next) => setBudget(Number(next) || 0)}
              aria-label={t('ai.autonomy.budget')}
            />
          </label>
          <p className="text-xs text-on-surface-variant">
            {t('ai.autonomy.budgetHint', { used: grant?.used_last_hour ?? 0 })}
          </p>
          <Button type="button" variant="secondary" disabled={busy} onClick={() => void save(true, budget)}>
            {t('ai.autonomy.save')}
          </Button>
        </div>
      )}

      <p className="mt-4 flex gap-2 rounded-lg border border-status-warning/30 bg-status-warning/10 p-3 text-xs leading-5 text-status-warning">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
        {t('ai.autonomy.boundary')}
      </p>
    </section>
  )
}
