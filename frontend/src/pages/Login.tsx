import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import type { User } from '@/types'
import { Logo } from '@/components/Logo'
import { VersionFooter } from '@/components/VersionFooter'
import { ErrorMessage } from '@/components/ui/ErrorMessage'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { Shield, ArrowRight, Globe, KeyRound, Mail, Check } from 'lucide-react'
import { supportedLocales } from '@/config/locales'

export function Login() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { finishLogin } = useAuthStore()
  const [error, setError] = useState('')
  const [form, setForm] = useState({ username: '', password: '', otp: '' })
  const [requires2FA, setRequires2FA] = useState(false)
  const [useBackupCode, setUseBackupCode] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [requiresVerification, setRequiresVerification] = useState(false)
  const [verifyEmail, setVerifyEmail] = useState('')
  const [verifyCode, setVerifyCode] = useState('')
  const [verifiedSuccess, setVerifiedSuccess] = useState(false)
  const [pendingVerifiedUser, setPendingVerifiedUser] = useState<User | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)

    try {
      const res = await api<{ access_token: string; requires_2fa: boolean; requires_verification: boolean; email: string }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          password: form.password,
          otp_code: form.otp || null,
        }),
      })

      if (res.requires_verification) {
        setRequiresVerification(true)
        setVerifyEmail(res.email)
        setSubmitting(false)
        return
      }

      if (res.requires_2fa) {
        setRequires2FA(true)
        setSubmitting(false)
        return
      }

      const user = await api<User>('/auth/me')
      await finishLogin(user)
      navigate('/')
    } catch (err: any) {
      setError(err.message || t('auth.loginFailed'))
      setSubmitting(false)
    }
  }

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const res = await api<{ requires_2fa: boolean }>('/auth/login-verify', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          password: form.password,
          code: verifyCode,
          otp_code: form.otp || null,
        }),
      })
      if (res.requires_2fa) {
        setRequires2FA(true)
        setRequiresVerification(false)
        setVerifyCode('')
        return
      }
      const user = await api<User>('/auth/me')
      setPendingVerifiedUser(user)
      setVerifiedSuccess(true)
      setRequiresVerification(false)
      setVerifyCode('')
    } catch (err: any) {
      setError(err.message || t('setup.verificationFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleResendCode = async () => {
    setError('')
    setSubmitting(true)
    try {
      await api('/auth/resend-verification', {
        method: 'POST',
        body: JSON.stringify({ email: verifyEmail }),
      })
    } catch (err: any) {
      setError(err.message || t('auth.resendFailed', 'Code konnte nicht erneut gesendet werden'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      <div className="absolute inset-0 msm-deep-grid opacity-50" />

      <div className="relative z-10 w-full max-w-md">
        <div className="flex justify-end mb-4">
          <div className="flex items-center gap-1.5 text-on-surface-variant hover:text-primary transition-colors relative">
            <Globe className="w-3.5 h-3.5 absolute left-1.5 pointer-events-none" />
            <select
              value={i18n.language}
              onChange={(e) => i18n.changeLanguage(e.target.value)}
              className="bg-transparent border-0 text-xs font-label-md pl-6 pr-4 py-1.5 cursor-pointer focus:outline-none focus:ring-0 text-on-surface-variant hover:text-primary transition-colors appearance-none"
              style={{ paddingRight: '1rem' }}
            >
              {supportedLocales.map((locale) => (
                <option key={locale.code} value={locale.code} className="bg-surface-container-high text-on-surface">
                  {locale.nativeLabel}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex items-center justify-center gap-3 mb-8">
          <Logo size="md" />
          <div>
            <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">
              MauntingStudios
            </h1>
            <p className="font-mono-sm text-mono-sm text-on-surface-variant">
              Infrastructure Control
            </p>
          </div>
        </div>

        <div className="msm-card p-8">
          {/* Verification success screen */}
          {verifiedSuccess && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-full bg-status-success/10 border border-status-success/30 flex items-center justify-center mx-auto mb-6">
                <Check className="w-8 h-8 text-status-success" />
              </div>
              <h2 className="font-headline text-headline-md text-primary mb-3">
                {t('auth.registerSuccess')}
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant mb-8">
                {t('auth.verifiedAndSignedIn')}
              </p>
              <button
                onClick={() => {
                  if (!pendingVerifiedUser) return
                  void finishLogin(pendingVerifiedUser).then(() => navigate('/'))
                }}
                className="msm-btn-primary px-8 py-3 inline-flex items-center gap-2"
              >
                {t('auth.continue')}
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Verification dialog */}
          {requiresVerification && !verifiedSuccess && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto mb-6">
                <Mail className="w-8 h-8 text-secondary" />
              </div>
              <h2 className="font-headline text-headline-md text-primary mb-3">
                {t('auth.emailNotVerified')}
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant mb-2 max-w-sm mx-auto">
                {t('setup.verifyEmailDesc', { email: verifyEmail })}
              </p>
              <p className="font-mono-sm text-mono-sm text-on-surface-variant mb-8">
                {t('setup.codeExpires')}
              </p>

              <form onSubmit={handleVerify} className="space-y-4 max-w-xs mx-auto">
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('auth.verificationCode')}
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="\d{6}"
                    maxLength={6}
                    value={verifyCode}
                    onChange={(e) => setVerifyCode(e.target.value)}
                    className="msm-input text-center text-2xl tracking-[0.5em] font-mono"
                    placeholder="000000"
                    required
                  />
                </div>

                <ErrorMessage message={error} className="text-sm" />

                <button
                  type="submit"
                  disabled={submitting || verifyCode.length !== 6}
                  className="msm-btn-primary w-full py-3 disabled:opacity-50"
                >
                  {submitting ? (
                    <span className="inline-flex items-center gap-2">
                      <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                      {t('common.loading')}
                    </span>
                  ) : (
                    t('auth.verifyNow')
                  )}
                </button>

                <button
                  type="button"
                  onClick={handleResendCode}
                  disabled={submitting}
                  className="text-sm text-secondary hover:text-mint-accent transition-colors disabled:opacity-50"
                >
                  {t('auth.resendCode')}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setRequiresVerification(false)
                    setVerifyCode('')
                    setError('')
                  }}
                  className="text-sm text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-50 block mx-auto"
                >
                  {t('auth.goToLogin')}
                </button>
              </form>
            </div>
          )}

          {/* Normal login form */}
          {!requiresVerification && !verifiedSuccess && (
            <>
              <div className="text-center mb-6">
                <div className="w-12 h-12 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto mb-4">
                  <Shield className="w-6 h-6 text-secondary" />
                </div>
                <h2 className="font-headline text-headline-md text-primary mb-1">
                  {t('auth.login')}
                </h2>
                <p className="font-body-md text-sm text-on-surface-variant">
                  {t('auth.loginDescription')}
                </p>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('auth.username')}
              </label>
              <input
                type="text"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                className="msm-input"
                placeholder="admin"
                required
                disabled={requires2FA}
              />
            </div>

            <PasswordInput
              label={t('auth.password') || 'Passwort'}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="••••••••"
              required
              disabled={requires2FA}
            />

            {requires2FA && (
              <>
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {useBackupCode
                      ? t('auth.backupCode', 'Backup-Code')
                      : t('auth.otpCode', '2FA-Code')}
                  </label>
                  <input
                    type="text"
                    value={form.otp}
                    onChange={(e) => setForm({ ...form, otp: e.target.value })}
                    className="msm-input"
                    placeholder={useBackupCode ? 'XXXX-XXXX' : '000000'}
                    required
                    maxLength={useBackupCode ? 12 : 6}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setUseBackupCode(!useBackupCode)
                    setForm({ ...form, otp: '' })
                  }}
                  className="text-xs text-secondary hover:text-mint-accent transition-colors flex items-center gap-1"
                >
                  <KeyRound className="w-3 h-3" />
                  {useBackupCode
                    ? t('auth.use2FAInstead', '2FA-Code stattdessen verwenden')
                    : t('auth.useBackupCode', 'Backup-Code verwenden')}
                </button>
              </>
            )}

            <ErrorMessage message={error} className="text-sm" />

            <button
              type="submit"
              disabled={submitting}
              className="msm-btn-primary w-full py-3 flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-2">
                  <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                  {t('common.loading')}
                </span>
              ) : (
                <>
                  {requires2FA ? t('auth.verify2FA') : t('auth.signIn')}
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

              <div className="mt-6 pt-6 border-t border-outline-variant/30 flex justify-between font-body-md text-sm">
                <Link
                  to="/register"
                  className="text-secondary hover:text-mint-accent transition-colors"
                >
                  {t('auth.noAccount')}
                </Link>
                <Link
                  to="/forgot-password"
                  className="text-on-surface-variant hover:text-on-surface transition-colors"
                >
                  {t('auth.forgotPassword')}
                </Link>
              </div>
            </>
          )}
        </div>

        <VersionFooter />
      </div>
    </div>
  )
}
