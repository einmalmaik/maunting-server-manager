import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { api } from '@/api/client'
import { Shield, Check, AlertTriangle, Download, RotateCcw } from 'lucide-react'

/**
 * Tab: Zwei-Faktor-Authentifizierung (TOTP).
 *
 * Verwaltet den gesamten 2FA-Lifecycle in einem Tab:
 *  - Setup-Flow mit QR-Code + Secret
 *  - Backup-Codes (Download + Regenerate)
 *  - Disable-Flow mit OTP-Bestaetigung
 *
 * Der authStore wird aktualisiert, damit andere Tabs (Password) wissen,
 * ob 2FA aktiv ist.
 */
export function TwoFactorTab() {
  const { t } = useTranslation()
  const { user, setUser } = useAuthStore()
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const [show2FASetup, setShow2FASetup] = useState(false)
  const [show2FADisable, setShow2FADisable] = useState(false)
  const [otpCode, setOtpCode] = useState('')
  const [faSecret, setFaSecret] = useState('')
  const [faUri, setFaUri] = useState('')
  const [backupCodes, setBackupCodes] = useState<string[]>([])

  const handleSetup2FA = async () => {
    setError('')
    try {
      const res = await api<{ secret: string; uri: string }>('/auth/2fa/setup', { method: 'POST' })
      setFaSecret(res.secret)
      setFaUri(res.uri)
      setShow2FASetup(true)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const handleEnable2FA = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await api('/auth/2fa/enable?otp_code=' + otpCode, { method: 'POST' })
      const codesRes = await api<{ codes: string[] }>('/auth/2fa/backup/generate', { method: 'POST' })
      setBackupCodes(codesRes.codes)
      const updated = await api<{ two_factor_enabled: boolean }>('/auth/me')
      if (user && updated) {
        setUser({ ...user, two_factor_enabled: true })
      }
      setShow2FASetup(false)
      setOtpCode('')
      setSuccess(t('profile.2faEnabled'))
      setTimeout(() => setSuccess(''), 5000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleDisable2FA = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await api('/auth/2fa/disable?otp_code=' + otpCode, { method: 'POST' })
      if (user) {
        setUser({ ...user, two_factor_enabled: false })
      }
      setShow2FADisable(false)
      setOtpCode('')
      setSuccess(t('profile.2faDisabled'))
      setTimeout(() => setSuccess(''), 3000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleRegenerateBackupCodes = async () => {
    setError('')
    setSubmitting(true)
    try {
      const codesRes = await api<{ codes: string[] }>('/auth/2fa/backup/generate', { method: 'POST' })
      setBackupCodes(codesRes.codes)
      setSuccess(t('profile.backupCodesRegenerated'))
      setTimeout(() => setSuccess(''), 5000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleDownloadBackupCodes = () => {
    if (backupCodes.length === 0) return
    const blob = new Blob([backupCodes.join('\n')], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    const date = new Date().toISOString().slice(0, 10)
    link.href = url
    link.download = `msm-backup-codes-${date}.txt`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    setBackupCodes([])
    setSuccess(t('profile.backupCodesDownloaded'))
    setTimeout(() => setSuccess(''), 5000)
  }

  return (
    <div className="msm-card p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
          <Shield className="w-5 h-5 text-secondary" />
        </div>
        <div className="flex-1">
          <h2 className="font-headline text-headline-sm text-primary">{t('profile.2faStatus')}</h2>
          <p className="font-body-md text-sm text-on-surface-variant mt-1">
            {user?.two_factor_enabled ? t('profile.2faEnabled') : t('profile.2faDisabled')}
          </p>
        </div>
        {user?.two_factor_enabled ? (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-status-success/10 text-status-success border border-status-success/30">
            <Check className="w-3 h-3" />
            {t('profile.2faEnabled')}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-status-error/10 text-status-error border border-status-error/30">
            <AlertTriangle className="w-3 h-3" />
            {t('profile.2faDisabled')}
          </span>
        )}
      </div>

      {error && <div className="msm-alert-error text-sm mb-4">{error}</div>}
      {success && <div className="msm-alert-success text-sm mb-4">{success}</div>}

      {!user?.two_factor_enabled && !show2FASetup && (
        <button
          onClick={handleSetup2FA}
          className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2"
        >
          <Shield className="w-4 h-4" />
          {t('profile.2faSetup')}
        </button>
      )}

      {user?.two_factor_enabled && !show2FADisable && (
        <div className="flex flex-wrap gap-3">
          <button
            onClick={handleRegenerateBackupCodes}
            disabled={submitting}
            className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
          >
            <RotateCcw className="w-4 h-4" />
            {t('profile.regenerateBackupCodes')}
          </button>
          <button
            onClick={() => setShow2FADisable(true)}
            className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2"
          >
            <Shield className="w-4 h-4" />
            {t('profile.2faDisable')}
          </button>
        </div>
      )}

      {show2FASetup && (
        <div className="mt-4 space-y-4 border-t border-outline-variant/30 pt-4">
          <p className="font-body-md text-sm text-on-surface-variant">{t('profile.2faScan')}</p>
          {faUri && (
            <div className="flex flex-col items-center gap-4">
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(faUri)}`}
                alt={t('profile.2faQrCode', '2FA QR Code')}
                className="rounded-lg border border-outline-variant"
              />
              <p className="font-mono-sm text-mono-sm text-on-surface-variant bg-surface-container-high px-3 py-1.5 rounded border border-outline-variant select-all">
                {faSecret}
              </p>
            </div>
          )}
          <form onSubmit={handleEnable2FA} className="mx-auto flex max-w-xs flex-col gap-3">
            <label className="font-body-md text-sm text-on-surface-variant text-center">
              {t('profile.2faEnterCode')}
            </label>
            <input
              type="text"
              inputMode="numeric"
              pattern="\d{6}"
              maxLength={6}
              value={otpCode}
              onChange={(e) => setOtpCode(e.target.value)}
              className="msm-input text-center text-xl tracking-[0.5em] font-mono"
              placeholder="000000"
              required
            />
            <button
              type="submit"
              disabled={submitting || otpCode.length !== 6}
              className="msm-btn-primary px-4 py-2 disabled:opacity-50 whitespace-nowrap"
            >
              {submitting ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                t('common.save')
              )}
            </button>
          </form>
        </div>
      )}

      {show2FADisable && (
        <div className="mt-4 space-y-4 border-t border-outline-variant/30 pt-4">
          <p className="font-body-md text-sm text-on-surface-variant">{t('profile.2faEnterCode')}</p>
          <form onSubmit={handleDisable2FA} className="flex gap-3 max-w-xs">
            <input
              type="text"
              inputMode="numeric"
              pattern="\d{6}"
              maxLength={6}
              value={otpCode}
              onChange={(e) => setOtpCode(e.target.value)}
              className="msm-input text-center text-xl tracking-[0.5em] font-mono"
              placeholder="000000"
              required
            />
            <button
              type="submit"
              disabled={submitting || otpCode.length !== 6}
              className="msm-btn-primary px-4 py-2 disabled:opacity-50 whitespace-nowrap"
            >
              {submitting ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                t('common.save')
              )}
            </button>
          </form>
          <button
            type="button"
            onClick={() => { setShow2FADisable(false); setOtpCode('') }}
            className="text-sm text-on-surface-variant hover:text-on-surface transition-colors"
          >
            {t('common.cancel')}
          </button>
        </div>
      )}

      {backupCodes.length > 0 && (
        <div className="mt-4 p-4 bg-status-warning/5 border border-status-warning/20 rounded-lg">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-4 h-4 text-status-warning" />
            <p className="font-label-md text-sm text-status-warning font-medium">{t('profile.backupCodes')}</p>
          </div>
          <p className="font-body-md text-xs text-on-surface-variant mb-3">{t('profile.backupCodesWarning')}</p>
          <p className="font-body-md text-xs text-on-surface-variant mb-3">
            {t('profile.backupCodesDownloadOnce')}
          </p>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={handleDownloadBackupCodes}
              className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2"
            >
              <Download className="w-4 h-4" />
              {t('profile.downloadBackupCodes')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
