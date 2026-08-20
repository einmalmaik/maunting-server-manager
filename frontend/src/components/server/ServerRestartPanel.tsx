import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, CalendarClock, CheckCircle2, Plus, Save, Trash2, XCircle } from 'lucide-react'
import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Dropdown, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import type { Server } from '@/types'
import { formatPanelDateTime, formatPanelTime, type PanelTimeFormat } from '@/utils/timeFormat'

interface Props {
  server: Server
  serverId: number
  onSaved: () => void
}

const INTERVAL_OPTIONS = [1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 168]
const TIME_OPTIONS = Array.from({ length: 48 }, (_, i) => {
  const hour = String(Math.floor(i / 2)).padStart(2, '0')
  const minute = i % 2 === 0 ? '00' : '30'
  return `${hour}:${minute}`
})

export function ServerRestartPanel({ server, serverId, onSaved }: Props) {
  const { t, i18n } = useTranslation()
  const canWrite = useHasPermission('server.config.write', serverId)
  const [timeFormat, setTimeFormat] = useState<PanelTimeFormat>('24h')
  const [enabled, setEnabled] = useState(server.auto_restart)
  const [mode, setMode] = useState<'interval' | 'fixed'>(
    server.restart_interval_hours ? 'interval' : 'fixed',
  )
  const [intervalHours, setIntervalHours] = useState(server.restart_interval_hours || 4)
  const [times, setTimes] = useState<string[]>(() => {
    const raw = server.restart_times_utc || server.restart_time_utc || '04:00'
    return raw.split(',').map((part) => part.trim()).filter(Boolean)
  })
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api<{ time_format: PanelTimeFormat }>('/settings')
      .then((data) => setTimeFormat(data.time_format === '12h' ? '12h' : '24h'))
      .catch(() => setTimeFormat('24h'))
  }, [])

  const sortedTimes = useMemo(() => [...new Set(times)].sort(), [times])

  // Optionen fuer die Dropdown-Bausteine (Design-DNA: kein natives <select>).
  // DropdownOption.value ist strikt string — Zahlen werden an der Nahtstelle
  // gewandelt. Die Uhrzeit-Labels haengen am Panel-Zeitformat und duerfen
  // deshalb keine Modul-Konstante sein. Der gespeicherte Wert kann ausserhalb
  // des Rasters liegen (die KI darf z. B. 5 Stunden oder 04:17 setzen) und
  // wird deshalb als Option eingespeist — sonst zeigte der Trigger statt des
  // gesetzten Werts nur den Platzhalter.
  const intervalOptionen = useMemo(() => {
    const stunden = INTERVAL_OPTIONS.includes(intervalHours)
      ? INTERVAL_OPTIONS
      : [...INTERVAL_OPTIONS, intervalHours].sort((a, b) => a - b)
    return stunden.map((hours) => ({
      value: String(hours),
      label: t('restarts.everyHours', { count: hours }),
    }))
  }, [t, intervalHours])
  const zeitOptionen = useMemo(() => {
    const werte = [...new Set([...TIME_OPTIONS, ...times])].sort()
    return werte.map((option) => ({
      value: option,
      label: formatPanelTime(option, timeFormat),
    }))
  }, [timeFormat, times])

  const save = async () => {
    setSaving(true)
    const fixedTimes = sortedTimes.length ? sortedTimes.join(',') : '04:00'
    try {
      await api<Server>(`/servers/${serverId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          auto_restart: enabled,
          restart_interval_hours: enabled && mode === 'interval' ? intervalHours : null,
          restart_time_utc: enabled && mode === 'fixed' ? fixedTimes.split(',')[0] : null,
          restart_times_utc: enabled && mode === 'fixed' ? fixedTimes : null,
        }),
      })
      toast.success(t('restarts.saved'))
      onSaved()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setSaving(false)
    }
  }

  const addTime = () => {
    if (times.length >= 12) return
    const next = TIME_OPTIONS.find((option) => !times.includes(option)) || '04:00'
    setTimes([...times, next])
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3 flex-wrap">
          <p className="font-body-md text-body-md text-on-surface-variant">{t('restarts.subtitle')}</p>
          {server.restart_ai_managed && (
            <span
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border border-primary/40 bg-primary/10 text-primary"
              title={t('restarts.aiManagedHint')}
            >
              <Bot className="w-3.5 h-3.5" />
              {t('restarts.aiManaged')}
            </span>
          )}
        </div>
        <button
          onClick={save}
          disabled={saving || !canWrite}
          className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2 disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {saving ? t('common.loading') : t('common.save')}
        </button>
      </div>

      <div className="msm-card p-5 space-y-5">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 border-b border-outline pb-5">
          <div className="border-l border-outline pl-3">
            <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2 inline-flex items-center gap-2">
              <CalendarClock className="w-3.5 h-3.5" />
              {t('restarts.lastAutoAttempt', { defaultValue: 'Letzter Versuch' })}
            </p>
            <p className="font-body-md text-sm text-on-surface">
              {formatPanelDateTime(server.last_auto_restart_attempt_at, timeFormat, i18n.language)}
            </p>
          </div>
          <div className="border-l border-outline pl-3">
            <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">
              {t('restarts.lastAutoRestart', { defaultValue: 'Letzter Auto-Restart' })}
            </p>
            <p className="font-body-md text-sm text-on-surface">
              {formatPanelDateTime(server.last_auto_restart_completed_at, timeFormat, i18n.language)}
            </p>
          </div>
          <div className="border-l border-outline pl-3">
            <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">
              {t('restarts.nextAutoRestart', { defaultValue: 'Nächster Auto-Restart' })}
            </p>
            <div className="flex items-center gap-2">
              {server.last_auto_restart_status === 'success' && <CheckCircle2 className="w-3.5 h-3.5 text-status-success" />}
              {server.last_auto_restart_status === 'failed' && <XCircle className="w-3.5 h-3.5 text-status-destructive" />}
              <p className="font-body-md text-sm text-on-surface">
                {formatPanelDateTime(server.next_auto_restart_at, timeFormat, i18n.language)}
              </p>
            </div>
          </div>
        </div>

        {/* Hier stand ein von Hand nachgebauter Umschalter: eine sr-only-Checkbox
            mit zwei <span> als Optik. Das `disabled` saß dabei nur auf der
            unsichtbaren Checkbox — die sichtbare Bahn, der Knubbel und das
            `cursor-pointer` des Labels blieben unverändert. Ohne
            `server.config.write` sah der Schalter also voll bedienbar aus, der
            Mauszeiger versprach es, und der Klick verpuffte wortlos; das darunter
            liegende <fieldset> dimmt zwar, den Schalter selbst erreicht es nicht,
            denn er steht außerhalb.

            `Switch` aus @/Singra/UI trägt `disabled:opacity-50` und
            `disabled:cursor-not-allowed` und meldet sich als `role="switch"` mit
            `aria-checked`. Der gesperrte Zustand ist damit sichtbar UND für
            Bedienungshilfen lesbar — beides hat der Nachbau nicht geleistet.
            Das `cursor-pointer` am Label fällt weg: es hätte weiter Bedienbarkeit
            versprochen, die es bei Lesezugriff nicht gibt. */}
        <label className="inline-flex items-center gap-3">
          <Switch
            checked={enabled}
            onCheckedChange={setEnabled}
            disabled={!canWrite}
            aria-label={t('restarts.enabled')}
          />
          <span className="font-body-md text-sm text-on-surface">{t('restarts.enabled')}</span>
        </label>

        <fieldset disabled={!enabled || !canWrite} className="space-y-5 border-0 p-0 m-0 disabled:opacity-60">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => setMode('interval')}
              className={`rounded-md border px-4 py-3 text-left transition-colors ${
                mode === 'interval'
                  ? 'border-secondary bg-secondary/10 text-primary'
                  : 'border-outline bg-surface-container-high text-on-surface-variant'
              }`}
            >
              <span className="block font-headline text-sm">{t('restarts.modeInterval')}</span>
              <span className="block font-body-md text-xs mt-1">{t('restarts.modeIntervalHint')}</span>
            </button>
            <button
              type="button"
              onClick={() => setMode('fixed')}
              className={`rounded-md border px-4 py-3 text-left transition-colors ${
                mode === 'fixed'
                  ? 'border-secondary bg-secondary/10 text-primary'
                  : 'border-outline bg-surface-container-high text-on-surface-variant'
              }`}
            >
              <span className="block font-headline text-sm">{t('restarts.modeFixed')}</span>
              <span className="block font-body-md text-xs mt-1">{t('restarts.modeFixedHint')}</span>
            </button>
          </div>

          {mode === 'interval' ? (
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('restarts.interval')}
              </label>
              {/* Das Fieldset sperrt den Trigger-Button auch nativ; das
                  explizite disabled haelt zusaetzlich den internen Guard der
                  Komponente und macht den Zustand ohne Fieldset-Wissen
                  testbar. Die eigene Dimmung des Triggers ist hier abgeschaltet,
                  weil das Fieldset schon dimmt — sonst stapeln sich 60 % und
                  50 % zu einem kaum lesbaren Wert. */}
              <Dropdown
                value={String(intervalHours)}
                onChange={(wert) => setIntervalHours(Number(wert))}
                options={intervalOptionen}
                disabled={!enabled || !canWrite}
                className="max-w-xs"
                buttonClassName="disabled:opacity-100"
                aria-label={t('restarts.interval')}
              />
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <label className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
                  {t('restarts.fixedTimes')}
                </label>
                <button
                  type="button"
                  onClick={addTime}
                  disabled={times.length >= 12}
                  className="msm-btn-secondary inline-flex items-center gap-2 px-3 py-1.5 text-sm disabled:opacity-50"
                >
                  <Plus className="w-3.5 h-3.5" />
                  {t('restarts.addTime')}
                </button>
                {times.length >= 12 && (
                  <span className="text-xs text-on-surface-variant">{t('restarts.maxTimesReached') || 'Max. 12 Zeiten erreicht'}</span>
                )}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {times.map((time, index) => (
                  <div key={`${time}-${index}`} className="flex gap-2">
                    <Dropdown
                      value={time}
                      onChange={(wert) => {
                        const next = [...times]
                        next[index] = wert
                        setTimes(next)
                      }}
                      options={zeitOptionen}
                      disabled={!enabled || !canWrite}
                      buttonClassName="disabled:opacity-100"
                      aria-label={t('restarts.fixedTimes')}
                    />
                    <button
                      type="button"
                      onClick={() => setTimes(times.filter((_, i) => i !== index))}
                      disabled={times.length <= 1}
                      className="msm-btn-secondary px-3 disabled:opacity-50"
                      title={t('common.delete')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </fieldset>
      </div>
    </div>
  )
}
