import { useCallback, useEffect, useId, useRef, useState } from 'react'
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
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  // Unser `Dropdown` und der `NumberStepper` sind Knopfgruppen, keine
  // `<select>`/`<input>`-Elemente. Ein umschliessendes `<label>` beschriftet
  // sie deshalb nicht, sondern leitet den Klick an das erste bedienbare Element
  // darin weiter — beim Stepper ist das der **Minus**-Knopf.
  const panelId = useId()
  const scopeId = useId()
  const budgetId = useId()

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
      const ziel = event.target as HTMLElement | null
      if (!ziel || !rootRef.current) return
      // Das Optionsmenue unseres `Dropdown` haengt per Portal an
      // `document.body` und liegt damit ausserhalb von `rootRef`. Ohne diese
      // zweite Pruefung schloss ein Klick auf eine Option das Panel — und zwar
      // auf `mousedown`, also **bevor** das `click` der Option feuerte.
      // `setScope` lief nie, der Bereich blieb auf „Panelweit“, und eine
      // serverbezogene Freigabe liess sich ueber die Oberflaeche gar nicht
      // einstellen.
      if (ziel.closest?.('[data-msm-dropdown-menu]')) return
      if (!rootRef.current.contains(ziel)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      // Der Fokus fiel beim Schliessen auf `<body>`: der naechste Tab begann
      // wieder ganz oben auf der Seite. Er gehoert dorthin zurueck, wo er
      // herkam.
      triggerRef.current?.focus()
    }
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
    // Auch das **Aus**schalten fragt nach, seit die Freigabe mehr entscheidet
    // als „ohne Rueckfrage ausfuehren“: sie ist der Schalter, der die
    // Guardian-Engine die KI wecken laesst. Wer sie abschaltet, hat danach
    // Server, die nachts stehen bleiben, bis jemand hinsieht — das gehoert
    // gesagt, bevor es passiert, und nicht erst in der Stoerungsmeldung.
    // Nicht `danger`: rot bedeutet in MSM unumkehrbar, und hier wird nur eine
    // Erlaubnis zurueckgenommen, die man jederzeit wieder erteilen kann.
    if (!nextEnabled && enabled) {
      const accepted = await confirm({
        title: t('ai.autonomy.disableTitle'),
        message: t(serverId === null ? 'ai.autonomy.disablePanel' : 'ai.autonomy.disableServer'),
        confirmText: t('ai.autonomy.disable'),
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
        ref={triggerRef}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-controls={panelId}
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
        <div id={panelId} className="absolute left-0 top-full z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-outline-variant bg-surface-container-high p-4 shadow-panel">
          <h3 className="text-sm font-semibold text-on-surface">{t('ai.autonomy.title')}</h3>
          <p className="mt-1 text-xs leading-5 text-on-surface-variant">
            {/* Der Text stand fest auf „panelweit ... auf allen deinen Servern“
                und beschrieb damit genau den Fall, den jemand gerade abgewaehlt
                hatte. Die Verzweigung gab es in `save` (Bestaetigungsdialog)
                laengst, nur hier nicht — und `descriptionServer` lag deshalb
                unbenutzt in allen Sprachdateien. */}
            {t(serverId === null ? 'ai.autonomy.descriptionPanel' : 'ai.autonomy.descriptionServer')}
          </p>

          <div className="mt-3 space-y-1.5">
            <label htmlFor={scopeId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.autonomy.scope')}
            </label>
            <Dropdown
              id={scopeId}
              value={scope}
              onChange={setScope}
              options={[
                { value: PANEL_SCOPE, label: t('ai.autonomy.scopePanel') },
                ...servers.map((server) => ({ value: String(server.id), label: server.name })),
              ]}
              disabled={busy}
              aria-label={t('ai.autonomy.scope')}
            />
          </div>

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
              <div>
                <label htmlFor={budgetId} className="mb-1 block text-xs text-on-surface-variant">
                  {t('ai.autonomy.budget')}
                </label>
                <NumberStepper
                  id={budgetId}
                  value={budget}
                  min={0}
                  max={1000}
                  step={1}
                  disabled={busy}
                  onValueChange={(next) => setBudget(Number(next) || 0)}
                  aria-label={t('ai.autonomy.budget')}
                />
              </div>
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
