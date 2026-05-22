import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { api } from '@/api/client'
import { Shield, Mail, KeyRound, Check, Save, AlertTriangle, Copy } from 'lucide-react'

export function Profile() {
  const { t } = useTranslation()
  const { user, setUser } = useAuthStore()
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const [pwdForm, setPwdForm] = useState({
    current: '',
    new: '',
    confirm: '',
    otp: '',
  })

  const [show2FASetup, setShow2FASetup] = useState(false)
  const [show2FADisable, setShow2FADisable] = useState(false)
  const [otpCode, setOtpCode] = useState('')
  const [faSecret, setFaSecret] = useState('')
  const [faUri, setFaUri] = useState('')
  const [backupCodes, setBackupCodes] = useState<string[]>([])

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSuccess('')

    if (pwdForm.new !== pwdForm.confirm) {
      setError(t('profile.passwordMismatch'))
      return
    }
    if (pwdForm.new.length < 8) {
      setError(t('auth.passwordTooShort'))
      return
    }

    setSubmitting(true)
    try {
      await api('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: pwdForm.current,
          new_password: pwdForm.new,
          otp_code: user?.two_factor_enabled ? pwdForm.otp : null,
        }),
      })
      setSuccess(t('profile.passwordChanged'))
      setPwdForm({ current: '', new: '', confirm: '', otp: '' })
      setTimeout(() => setSuccess(''), 3000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleSetup2FA = async () => {
    setError('')
    try {
      const res = await api<{ secret: string; uri: string }>('/auth/2fa/setup', {
        method: 'POST',
      })
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
      // Backup-Codes generieren
      const codesRes = await api<{ codes: string[] }>('/auth/2fa/backup/generate', { method: 'POST' })
      setBackupCodes(codesRes.codes)
      // User-Data aktualisieren
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('profile.title')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          {t('profile.subtitle')}
        </p>
      </div>

      {error && <div className="msm-alert-error text-sm">{error}</div>}
      {success && <div className="msm-alert-success text-sm">{success}</div>}

      {/* Account Info */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <Mail className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('auth.email')}</h2>
        </div>
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-lg font-bold text-primary border border-outline-variant">
            {user?.username.charAt(0).toUpperCase()}
          </div>
          <div>
            <p className="font-label-md text-sm text-on-surface font-medium">{user?.username}</p>
            <p className="font-body-md text-sm text-on-surface-variant">{user?.email}</p>
            {user?.email_verified === false && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-status-error/10 text-status-error border border-status-error/30 mt-1">
                <AlertTriangle className="w-3 h-3" />
                Nicht verifiziert
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Change Password */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <KeyRound className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('profile.changePassword')}</h2>
        </div>

        <form onSubmit={handleChangePassword} className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2">
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('profile.currentPassword')}
            </label>
            <input
              type="password"
              value={pwdForm.current}
              onChange={(e) => setPwdForm({ ...pwdForm, current: e.target.value })}
              className="msm-input"
              required
            />
          </div>
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('profile.newPassword')}
            </label>
            <input
              type="password"
              value={pwdForm.new}
              onChange={(e) => setPwdForm({ ...pwdForm, new: e.target.value })}
              className="msm-input"
              required
              minLength={8}
            />
          </div>
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('profile.confirmPassword')}
            </label>
            <input
              type="password"
              value={pwdForm.confirm}
              onChange={(e) => setPwdForm({ ...pwdForm, confirm: e.target.value })}
              className="msm-input"
              required
              minLength={8}
            />
          </div>
          {user?.two_factor_enabled && (
            <div className="md:col-span-2">
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('auth.otpCode')}
              </label>
              <input
                type="text"
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                value={pwdForm.otp}
                onChange={(e) => setPwdForm({ ...pwdForm, otp: e.target.value })}
                className="msm-input"
                placeholder="000000"
                required
              />
            </div>
          )}
          <div className="md:col-span-2 flex justify-end">
            <button
              type="submit"
              disabled={submitting}
              className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {submitting ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('common.save')}
            </button>
          </div>
        </form>
      </div>

      {/* 2FA */}
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
          <button
            onClick={() => setShow2FADisable(true)}
            className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2"
          >
            <Shield className="w-4 h-4" />
            {t('profile.2faDisable')}
          </button>
        )}

        {/* 2FA Setup Flow */}
        {show2FASetup && (
          <div className="mt-4 space-y-4 border-t border-outline-variant/30 pt-4">
            <p className="font-body-md text-sm text-on-surface-variant">{t('profile.2faScan')}</p>
            {faUri && (
              <div className="flex flex-col items-center gap-4">
                <img
                  src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(faUri)}`}
                  alt="2FA QR Code"
                  className="rounded-lg border border-outline-variant"
                />
                <p className="font-mono-sm text-mono-sm text-on-surface-variant bg-surface-container-high px-3 py-1.5 rounded border border-outline-variant select-all">
                  {faSecret}
                </p>
              </div>
            )}
            <form onSubmit={handleEnable2FA} className="flex gap-3 max-w-xs">
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

        {/* 2FA Disable Flow */}
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

        {/* Backup Codes */}
        {backupCodes.length > 0 && (
          <div className="mt-4 p-4 bg-status-warning/5 border border-status-warning/20 rounded-lg">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle className="w-4 h-4 text-status-warning" />
              <p className="font-label-md text-sm text-status-warning font-medium">{t('profile.backupCodes')}</p>
            </div>
            <p className="font-body-md text-xs text-on-surface-variant mb-3">{t('profile.backupCodesWarning')}</p>
            <div className="grid grid-cols-2 gap-2">
              {backupCodes.map((code, i) => (
                <div
                  key={i}
                  className="font-mono text-sm bg-surface-container-high px-3 py-1.5 rounded border border-outline-variant flex items-center justify-between"
                >
                  <span>{code}</span>
                  <button
                    onClick={() => navigator.clipboard.writeText(code)}
                    className="text-on-surface-variant hover:text-primary transition-colors"
                    title="Kopieren"
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
