import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Save, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { toast } from '@/stores/toastStore'
import type { Server } from '@/types'
import { formatPanelTime, type PanelTimeFormat } from '@/utils/timeFormat'

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
  const { t } = useTranslation()
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
        <p className="font-body-md text-body-md text-on-surface-variant">{t('restarts.subtitle')}</p>
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
        <label className="inline-flex items-center gap-3 cursor-pointer">
          <span className={`relative w-10 h-6 rounded-full transition-colors ${enabled ? 'bg-secondary' : 'bg-surface-container-highest'}`}>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              disabled={!canWrite}
              className="sr-only"
            />
            <span className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-transform ${enabled ? 'translate-x-4 bg-on-secondary' : 'bg-on-surface'}`} />
          </span>
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
              <select
                value={intervalHours}
                onChange={(e) => setIntervalHours(Number(e.target.value))}
                className="msm-input max-w-xs"
              >
                {INTERVAL_OPTIONS.map((hours) => (
                  <option key={hours} value={hours}>
                    {t('restarts.everyHours', { count: hours })}
                  </option>
                ))}
              </select>
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
                    <select
                      value={time}
                      onChange={(e) => {
                        const next = [...times]
                        next[index] = e.target.value
                        setTimes(next)
                      }}
                      className="msm-input"
                    >
                      {TIME_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {formatPanelTime(option, timeFormat)}
                        </option>
                      ))}
                    </select>
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
