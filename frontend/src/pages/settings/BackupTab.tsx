import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Cloud, Save, Send, ShieldCheck, KeyRound } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PasswordInput } from '@/components/ui/PasswordInput'

/** S3-Konfiguration aus GET /api/backup-config (Credentials maskiert). */
interface S3Config {
  endpoint: string
  access_key: string // maskiert (letzte 4 Zeichen)
  secret_key: string // maskiert (letzte 4 Zeichen)
  bucket: string
  region: string
}

/** Backup-System-Status aus GET /api/backup-config/status. */
interface BackupStatus {
  s3_configured: boolean
  backup_password_set: boolean
  last_panel_backup: string | null
}

const EMPTY_CONFIG: S3Config = {
  endpoint: '',
  access_key: '',
  secret_key: '',
  bucket: '',
  region: '',
}

/**
 * BackupTab — S3-Konfiguration, Verbindungstest und Backup-Passwort.
 *
 * Admin-only (panel.settings.write). Alle Credentials werden vom Backend
 * verschluesselt gespeichert und in GET-Antworten maskiert zurueckgegeben.
 * Das Backup-Passwort ist write-only (wird nie ans Frontend gesendet).
 * Im Frontend werden keine Secrets geloggt oder in Toasts angezeigt.
 */
export function BackupTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')

  // S3-Formular-State. access_key/secret_key bleiben leer, bis der User
  // neue Werte eingibt (Backend liefert nur maskierte Werte zurueck, die
  // wir nicht ins Eingabefeld schreiben — sonst waere der maskierte
  // Platzhalter beim Speichern als "echter" Wert mistaken).
  const [config, setConfig] = useState<S3Config>(EMPTY_CONFIG)
  // Neue Werte, die der User explizit eingetragen hat (nur diese werden
  // beim Save an das Backend gesendet).
  const [newAccessKey, setNewAccessKey] = useState('')
  const [newSecretKey, setNewSecretKey] = useState('')

  const [status, setStatus] = useState<BackupStatus>({
    s3_configured: false,
    backup_password_set: false,
    last_panel_backup: null,
  })
  const [newPassword, setNewPassword] = useState('')

  const [loading, setLoading] = useState(true)
  const [savingS3, setSavingS3] = useState(false)
  const [testing, setTesting] = useState(false)
  const [savingPassword, setSavingPassword] = useState(false)

  const fetchConfig = async () => {
    try {
      const data = await api<S3Config>('/backup-config')
      // Maskierte Credentials nicht ins Eingabefeld laden — nur endpoint,
      // bucket und region sind nicht-sensitiv und koennen angezeigt werden.
      setConfig({
        endpoint: data.endpoint,
        access_key: '',
        secret_key: '',
        bucket: data.bucket,
        region: data.region,
      })
      setNewAccessKey('')
      setNewSecretKey('')
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  const fetchStatus = async () => {
    try {
      const data = await api<BackupStatus>('/backup-config/status')
      setStatus(data)
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  useEffect(() => {
    let active = true
    Promise.all([fetchConfig(), fetchStatus()])
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const handleSaveS3 = async (e: React.FormEvent) => {
    e.preventDefault()
    setSavingS3(true)
    try {
      await api('/backup-config/s3', {
        method: 'POST',
        body: JSON.stringify({
          endpoint: config.endpoint,
          access_key: newAccessKey,
          secret_key: newSecretKey,
          bucket: config.bucket,
          region: config.region,
        }),
      })
      toast.success(t('settings.backup.s3Saved'))
      setNewAccessKey('')
      setNewSecretKey('')
      await Promise.all([fetchConfig(), fetchStatus()])
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSavingS3(false)
    }
  }

  const handleTestConnection = async () => {
    setTesting(true)
    try {
      const res = await api<{ ok: boolean; message: string; bucket: string | null }>(
        '/backup-config/test-s3',
        { method: 'POST' },
      )
      toast[res.ok ? 'success' : 'error'](res.message)
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setTesting(false)
    }
  }

  const handleSavePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newPassword.trim()) return
    setSavingPassword(true)
    try {
      await api('/backup-config/password', {
        method: 'POST',
        body: JSON.stringify({ password: newPassword }),
      })
      toast.success(t('settings.backup.passwordSaved'))
      setNewPassword('')
      await fetchStatus()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSavingPassword(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <fieldset disabled={!canWrite} className="space-y-6 border-0 p-0 m-0">
      {/* Status Section */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <ShieldCheck className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('settings.backup.statusTitle')}</h2>
        </div>
        <div className="flex flex-wrap gap-4">
          <span className={`px-3 py-1.5 rounded-full text-sm font-medium border ${
            status.s3_configured
              ? 'bg-status-success/15 border-status-success/30 text-on-surface'
              : 'bg-surface-container-high border-border text-on-surface-variant'
          }`}>
            {status.s3_configured ? t('settings.backup.s3Configured') : t('settings.backup.s3NotConfigured')}
          </span>
          <span className={`px-3 py-1.5 rounded-full text-sm font-medium border ${
            status.backup_password_set
              ? 'bg-status-success/15 border-status-success/30 text-on-surface'
              : 'bg-surface-container-high border-border text-on-surface-variant'
          }`}>
            {status.backup_password_set ? t('settings.backup.passwordSet') : t('settings.backup.passwordNotSet')}
          </span>
        </div>
      </div>

      {/* S3 Config Form */}
      <form onSubmit={handleSaveS3} className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <Cloud className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('settings.backup.s3Title')}</h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="backup-s3-endpoint" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.backup.endpoint')}
            </label>
            <input
              id="backup-s3-endpoint"
              type="url"
              value={config.endpoint}
              onChange={(e) => setConfig({ ...config, endpoint: e.target.value })}
              placeholder="s3.us-west-004.backblazeb2.com"
              className="msm-input"
            />
          </div>

          <div>
            <label htmlFor="backup-s3-bucket" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.backup.bucket')}
            </label>
            <input
              id="backup-s3-bucket"
              type="text"
              value={config.bucket}
              onChange={(e) => setConfig({ ...config, bucket: e.target.value })}
              placeholder="mein-backup-bucket"
              className="msm-input"
            />
          </div>

          <div>
            <label htmlFor="backup-s3-access-key" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.backup.accessKey')}
            </label>
            <input
              id="backup-s3-access-key"
              type="text"
              value={newAccessKey}
              onChange={(e) => setNewAccessKey(e.target.value)}
              placeholder={config.access_key || t('settings.backup.accessKeyPlaceholder')}
              className="msm-input"
            />
          </div>

          <div>
            <label htmlFor="backup-s3-secret-key" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.backup.secretKey')}
            </label>
            <PasswordInput
              id="backup-s3-secret-key"
              value={newSecretKey}
              onChange={(e) => setNewSecretKey(e.target.value)}
              placeholder={t('settings.backup.secretKeyPlaceholder')}
            />
          </div>

          <div>
            <label htmlFor="backup-s3-region" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.backup.region')}
              <span className="ml-1.5 normal-case text-on-surface-variant/70">({t('settings.backup.optional')})</span>
            </label>
            <input
              id="backup-s3-region"
              type="text"
              value={config.region}
              onChange={(e) => setConfig({ ...config, region: e.target.value })}
              placeholder="eu-central-1"
              className="msm-input"
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-3 justify-end mt-6">
          <button
            type="button"
            onClick={handleTestConnection}
            disabled={testing || !status.s3_configured}
            className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
          >
            {testing ? (
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
            {t('settings.backup.testConnection')}
          </button>
          <button
            type="submit"
            disabled={savingS3 || !canWrite}
            className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
          >
            {savingS3 ? (
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {t('settings.backup.saveS3')}
          </button>
        </div>
      </form>

      {/* Backup Password Form */}
      <form onSubmit={handleSavePassword} className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <KeyRound className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('settings.backup.passwordTitle')}</h2>
        </div>

        <div className="flex items-center gap-2 mb-4">
          <span className={`w-2 h-2 rounded-full ${status.backup_password_set ? 'bg-status-success' : 'bg-on-surface-variant'}`} />
          <span className="font-body-md text-sm text-on-surface">
            {status.backup_password_set ? t('settings.backup.passwordSetLabel') : t('settings.backup.passwordNotSetLabel')}
          </span>
        </div>

        <div>
          <label htmlFor="backup-new-password" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('settings.backup.newPassword')}
          </label>
          <PasswordInput
            id="backup-new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder={t('settings.backup.newPasswordPlaceholder')}
          />
          <p className="font-body-md text-xs text-on-surface-variant mt-2">
            {t('settings.backup.passwordHint')}
          </p>
        </div>

        <div className="flex justify-end mt-4">
          <button
            type="submit"
            disabled={savingPassword || !newPassword.trim() || !canWrite}
            className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
          >
            {savingPassword ? (
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {t('settings.backup.savePassword')}
          </button>
        </div>
      </form>
    </fieldset>
  )
}
