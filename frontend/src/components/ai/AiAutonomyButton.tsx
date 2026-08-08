import { useCallback, useEffect, useRef, useState } from 'react'
import { ShieldAlert, Zap } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiAutonomyGrant } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, Dropdown, NumberStepper, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

const DEFAULT_BUDGET = 10
const PANEL_SCOPE = 'panel'

/**
 * Autonomer Modus als Schalter **im** Chat statt als Kasten daneben.
 *
 * Das Einschalten entfernt die Rueckfrage vor Aktionen, die Dateien aendern,
 * Server anlegen und Mods installieren koennen. Was es nicht entfernt, steht im
 * Hinweistext: Berechtigungen gelten unveraendert weiter, und destruktive
 * Aktionen bleiben immer bestaetigungspflichtig.
 */
export function AiAutonomyButton({
  servers,
  disabled = false,
}: {
  servers: Array<{ id: number; name: string }>
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [scope, setScope] = useState<string>(PANEL_SCOPE)
  const [grants, setGrants] = useState<AiAutonomyGrant[]>([])
  const [budget, setBudget] = useState(DEFAULT_BUDGET)
  const [busy, setBusy] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  const serverId = scope === PANEL_SCOPE ? null : Number(scope)
  const grant = grants.find((row) => row.server_id === serverId) ?? null
  const enabled = Boolean(grant?.enabled)
  // Fuer die Anzeige am Knopf zaehlt jede aktive Freigabe, nicht nur die
  // gerade im Panel ausgewaehlte.
  const anyEnabled = grants.some((row) => row.enabled)

  const load = useCallback(async () => {
    try {
      setGrants(await aiApi.listAutonomyGrants())
    } catch {
      // Fehlende Freigaben sind der Normalfall; ein Ladefehler darf den Chat
      // nicht mit einer Fehlermeldung ueberziehen.
      setGrants([])
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    setBudget(grant?.max_actions_per_hour ?? DEFAULT_BUDGET)
  }, [grant])

  useEffect(() => {
    if (!open) return
    const onClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

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
      setGrants((current) => [
        ...current.filter((row) => row.server_id !== serverId),
        saved,
      ])
      toast.success(t('ai.autonomy.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.autonomy.error'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-label={t('ai.autonomy.title')}
        className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
          anyEnabled
            ? 'border-status-warning/50 bg-status-warning/10 text-status-warning'
            : 'border-outline-variant/40 text-on-surface-variant hover:text-on-surface'
        }`}
      >
        <Zap className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="hidden sm:inline">{t('ai.autonomy.short')}</span>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-outline-variant bg-surface-container-high p-4 shadow-panel">
          <h3 className="text-sm font-semibold text-on-surface">{t('ai.autonomy.title')}</h3>
          <p className="mt-1 text-xs leading-5 text-on-surface-variant">
            {t('ai.autonomy.descriptionPanel')}
          </p>

          <label className="mt-3 block space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.autonomy.scope')}
            </span>
            <Dropdown
              value={scope}
              onChange={setScope}
              options={[
                { value: PANEL_SCOPE, label: t('ai.autonomy.scopePanel') },
                ...servers.map((server) => ({ value: String(server.id), label: server.name })),
              ]}
              disabled={busy}
              aria-label={t('ai.autonomy.scope')}
            />
          </label>

          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="text-sm text-on-surface">{t('ai.autonomy.toggle')}</span>
            <Switch
              checked={enabled}
              disabled={busy}
              onCheckedChange={(next) => void save(next, budget)}
              aria-label={t('ai.autonomy.toggle')}
            />
          </div>

          {enabled && (
            <div className="mt-3 space-y-2">
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">{t('ai.autonomy.budget')}</span>
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
              <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => void save(true, budget)}>
                {t('ai.autonomy.save')}
              </Button>
            </div>
          )}

          <p className="mt-3 flex gap-2 rounded-lg border border-status-warning/30 bg-status-warning/10 p-2.5 text-xs leading-5 text-status-warning">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t('ai.autonomy.boundary')}
          </p>
        </div>
      )}
    </div>
  )
}
