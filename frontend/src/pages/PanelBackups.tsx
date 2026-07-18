import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Database, Plus, Trash2, Cloud, CloudOff, Save, Settings as SettingsIcon, Wrench, Copy, Check, X, AlertTriangle } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { PageHeader } from '@/Singra/UI/PageHeader'

/** Panel-Backup-List-Item (GET /api/panel-backups). */
interface PanelBackupItem {
  id: number
  name: string | null
  size_mb: number | null
  db_type: string
  encrypted: boolean
  s3_status: 'cloud' | 'local'
  created_at: string
}

/** Panel-Backup-Settings (GET/PATCH /api/panel-backups/settings). */
interface PanelBackupSettings {
  enabled: boolean
  interval_hours: number
  retention_count: number
}

/** Restore-Vorbereitungs-Response (POST /api/panel-backups/{id}/prepare-restore). */
interface PrepareRestoreResult {
  script_path: string
  instructions: string
}

const INTERVAL_OPTIONS = [
  { value: 1, label: 'Stündlich' },
  { value: 2, label: 'Alle 2 Stunden' },
  { value: 3, label: 'Alle 3 Stunden' },
  { value: 6, label: 'Alle 6 Stunden' },
  { value: 12, label: 'Alle 12 Stunden' },
  { value: 24, label: 'Täglich' },
  { value: 48, label: 'Alle 2 Tage' },
  { value: 168, label: 'Wöchentlich' },
]

/**
 * PanelBackups — Panel-Self-Backup-Verwaltung.
 *
 * Admin-only (panel.settings.write via RequirePermission routeKey="panelBackups").
 * Listet Panel-Backups auf (Datum, Größe, S3-Status), erlaubt Erstellen,
 * Löschen (mit Bestätigungsdialog) und konfiguriert Scheduler/Retention.
 * Alle Texte deutsch mit Umlauten via i18n. Keine Secrets in UI/Toasts.
 */
export function PanelBackups() {
  const { t } = useTranslation()

  const [backups, setBackups] = useState<PanelBackupItem[]>([])
  const [settings, setSettings] = useState<PanelBackupSettings>({
    enabled: false,
    interval_hours: 24,
    retention_count: 7,
  })
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [preparingId, setPreparingId] = useState<number | null>(null)
  const [restoreResult, setRestoreResult] = useState<PrepareRestoreResult | null>(null)
  const [scriptCopied, setScriptCopied] = useState(false)

  const fetchBackups = useCallback(async () => {
    try {
      const data = await api<PanelBackupItem[]>('/panel-backups')
      setBackups(data)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('common.error'))
    }
  }, [t])

  const fetchSettings = useCallback(async () => {
    try {
      const data = await api<PanelBackupSettings>('/panel-backups/settings')
      setSettings(data)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('common.error'))
    }
  }, [t])

  useEffect(() => {
    let active = true
    Promise.all([fetchBackups(), fetchSettings()]).finally(() => {
      if (active) setLoading(false)
    })
    return () => {
      active = false
    }
  }, [fetchBackups, fetchSettings])

  const createBackup = async () => {
    setCreating(true)
    try {
      await api('/panel-backups', { method: 'POST', body: JSON.stringify({}) })
      toast.success(t('panelBackups.created'))
      await fetchBackups()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setCreating(false)
    }
  }

  const deleteBackup = async (id: number) => {
    if (
      !(await confirm({
        message: t('panelBackups.confirmDelete'),
        danger: true,
        confirmText: t('common.delete'),
      }))
    )
      return
    setDeletingId(id)
    try {
      await api(`/panel-backups/${id}`, { method: 'DELETE' })
      toast.success(t('panelBackups.deleted'))
      await fetchBackups()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setDeletingId(null)
    }
  }

  const prepareRestore = async (id: number) => {
    setPreparingId(id)
    setScriptCopied(false)
    try {
      const result = await api<PrepareRestoreResult>(
        `/panel-backups/${id}/prepare-restore`,
        { method: 'POST', body: JSON.stringify({}) },
      )
      setRestoreResult(result)
    } catch (err: unknown) {
      toast.error(
        err instanceof Error
          ? err.message
          : t('panelBackups.prepareRestoreFailed'),
      )
    } finally {
      setPreparingId(null)
    }
  }

  const closeRestoreModal = () => {
    setRestoreResult(null)
    setScriptCopied(false)
  }

  const copyScriptPath = async () => {
    if (!restoreResult) return
    try {
      await navigator.clipboard.writeText(restoreResult.script_path)
      setScriptCopied(true)
      window.setTimeout(() => setScriptCopied(false), 1500)
    } catch {
      // Clipboard ist Komfort, kein kritischer Pfad.
    }
  }

  const saveSettings = async () => {
    setSavingSettings(true)
    try {
      const updated = await api<PanelBackupSettings>('/panel-backups/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          enabled: settings.enabled,
          interval_hours: settings.interval_hours,
          retention_count: settings.retention_count,
        }),
      })
      setSettings(updated)
      toast.success(t('panelBackups.settingsSaved'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setSavingSettings(false)
    }
  }

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="w-6 h-6 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="msm-page">
      <PageHeader eyebrow={t('pageContext.panel', 'Panel')} title={t('panelBackups.title')} description={t('panelBackups.subtitle')} status={<span className="msm-badge-info">{backups.length} Backups</span>} actions={<div className="flex flex-wrap gap-2">
          <button
            onClick={() => setShowSettings(!showSettings)}
            className={`msm-btn-secondary flex min-h-11 items-center gap-2 px-3 py-2 ${showSettings ? 'bg-surface-container' : ''}`}
            title={t('panelBackups.settingsTitle')}
          >
            <SettingsIcon className="w-4 h-4" />
            {t('panelBackups.settingsButton')}
          </button>
          <button
            onClick={createBackup}
            disabled={creating}
            className="msm-btn-primary flex min-h-11 items-center gap-2 px-4 py-2 disabled:opacity-50"
          >
            {creating ? (
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <Plus className="w-4 h-4" />
            )}
            {creating ? t('common.loading') : t('panelBackups.create')}
          </button>
        </div>} />

      {/* Settings Section */}
      {showSettings && (
        <div className="msm-card p-5 space-y-4">
          <h2 className="font-headline text-body-lg text-on-surface">
            {t('panelBackups.settingsTitle')}
          </h2>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {/* Enabled toggle */}
            <label className="flex items-center gap-2 cursor-pointer self-end pb-1">
              <div
                className={`relative w-10 h-6 rounded-full transition-colors ${settings.enabled ? 'bg-secondary' : 'bg-surface-container-highest'}`}
              >
                <input
                  type="checkbox"
                  checked={settings.enabled}
                  onChange={(e) =>
                    setSettings({ ...settings, enabled: e.target.checked })
                  }
                  className="sr-only"
                />
                <span
                  className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-transform ${settings.enabled ? 'translate-x-4 bg-on-secondary' : 'bg-on-surface'}`}
                />
              </div>
              <span className="font-body-md text-sm text-on-surface-variant">
                {t('panelBackups.enabled')}
              </span>
            </label>

            {/* Interval */}
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {t('panelBackups.interval')}
              </label>
              <select
                value={settings.interval_hours}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    interval_hours: parseInt(e.target.value) || 24,
                  })
                }
                className="msm-input"
              >
                {INTERVAL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Retention */}
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {t('panelBackups.retention')}
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={settings.retention_count}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    retention_count: Math.max(1, parseInt(e.target.value) || 1),
                  })
                }
                className="msm-input"
              />
              <p className="mt-1 text-xs text-on-surface-variant">
                {t('panelBackups.retentionHint')}
              </p>
            </div>
          </div>

          <div className="flex justify-end">
            <button
              onClick={saveSettings}
              disabled={savingSettings}
              className="msm-btn-primary flex items-center gap-2 px-4 py-2 disabled:opacity-50"
            >
              {savingSettings ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {savingSettings ? t('common.loading') : t('common.save')}
            </button>
          </div>
        </div>
      )}

      {/* Backup List / Empty State */}
      {backups.length === 0 ? (
        <div className="msm-card p-12 text-center border-dashed border-2 border-outline-variant">
          <Database className="w-10 h-10 text-on-surface-variant mx-auto mb-4" />
          <h3 className="font-headline text-body-lg text-on-surface mb-1">
            {t('panelBackups.noBackups')}
          </h3>
          <p className="font-body-md text-sm text-on-surface-variant">
            {t('panelBackups.createHint')}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {backups.map((backup) => {
            const isCloud = backup.s3_status === 'cloud'
            return (
              <div
                key={backup.id}
                className="msm-card p-4 flex items-center justify-between"
              >
                <div className="flex items-center gap-4">
                  <Database className="w-5 h-5 text-on-surface-variant flex-shrink-0" />
                  <div>
                    {backup.name && (
                      <p className="font-headline text-sm text-on-surface">
                        {backup.name}
                      </p>
                    )}
                    <p className="font-body-md text-on-surface text-sm">
                      {formatDate(backup.created_at)}
                    </p>
                    <p className="font-mono-sm text-xs text-on-surface-variant">
                      {backup.size_mb != null ? `${backup.size_mb} MB` : '—'}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {isCloud ? (
                    <span title={t('panelBackups.cloudTooltip')} className="flex-shrink-0">
                      <Cloud className="w-5 h-5 text-status-success" />
                    </span>
                  ) : (
                    <span title={t('panelBackups.localTooltip')} className="flex-shrink-0">
                      <CloudOff className="w-5 h-5 text-on-surface-variant/40" />
                    </span>
                  )}
                  <button
                    onClick={() => prepareRestore(backup.id)}
                    disabled={preparingId === backup.id}
                    className="msm-btn-secondary flex items-center gap-1 px-3 py-1.5 text-sm disabled:opacity-50"
                    title={t('panelBackups.prepareRestore')}
                  >
                    {preparingId === backup.id ? (
                      <span className="w-3.5 h-3.5 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <Wrench className="w-3.5 h-3.5" />
                    )}
                    {preparingId === backup.id
                      ? t('panelBackups.prepareRestoreLoading')
                      : t('panelBackups.prepareRestore')}
                  </button>
                  <button
                    onClick={() => deleteBackup(backup.id)}
                    disabled={deletingId === backup.id}
                    className="msm-btn-danger flex items-center gap-1 px-3 py-1.5 text-sm disabled:opacity-50"
                    title={t('common.delete')}
                  >
                    {deletingId === backup.id ? (
                      <span className="w-3.5 h-3.5 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <Trash2 className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Restore Modal */}
      {restoreResult && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="panel-restore-modal-title"
        >
          <div className="msm-card w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6 space-y-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2
                  id="panel-restore-modal-title"
                  className="font-headline text-headline-sm text-primary"
                >
                  {t('panelBackups.restoreModalTitle')}
                </h2>
                <p className="font-body-md text-sm text-on-surface-variant mt-1">
                  {t('panelBackups.restoreModalSubtitle')}
                </p>
              </div>
              <button
                onClick={closeRestoreModal}
                className="msm-btn-secondary p-1.5"
                aria-label={t('panelBackups.restoreModalClose')}
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Warnung */}
            <div className="flex items-start gap-2 rounded-md border border-error/40 bg-error-container/20 p-3">
              <AlertTriangle className="w-5 h-5 text-error flex-shrink-0 mt-0.5" />
              <p className="font-body-md text-sm text-on-surface">
                {t('panelBackups.restoreModalWarning')}
              </p>
            </div>

            {/* Skript-Pfad (mono, kopierbar) */}
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {t('panelBackups.restoreModalScriptPath')}
              </label>
              <div className="flex items-stretch gap-2">
                <code className="font-mono-sm text-sm bg-surface-container-high px-3 py-2 rounded-md flex-1 break-all">
                  {restoreResult.script_path}
                </code>
                <button
                  onClick={copyScriptPath}
                  className="msm-btn-secondary flex items-center gap-1 px-3 py-2 text-sm"
                  title={t('panelBackups.restoreModalCopyScript')}
                >
                  {scriptCopied ? (
                    <Check className="w-3.5 h-3.5" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
                  {scriptCopied
                    ? t('common.copied')
                    : t('common.copy')}
                </button>
              </div>
            </div>

            {/* Anleitung (vom Backend generiert, deutsch mit Warnung + sudo bash) */}
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {t('panelBackups.restoreModalInstructions')}
              </label>
              <pre className="font-mono-sm text-sm bg-surface-container-high px-3 py-3 rounded-md whitespace-pre-wrap break-words text-on-surface">
                {restoreResult.instructions}
              </pre>
            </div>

            <div className="flex justify-end pt-1">
              <button
                onClick={closeRestoreModal}
                className="msm-btn-primary px-4 py-2"
              >
                {t('panelBackups.restoreModalClose')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
