import { useState, useEffect } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { apiUrl } from '@/config/api'
import { useAuthStore } from '@/stores/authStore'
import { oauthApi, type OAuthProviderPublic } from '@/api/oauth'
import { toast } from '@/stores/toastStore'
import type { User } from '@/types'
import { Logo } from '@/components/Logo'
import { VersionFooter } from '@/components/VersionFooter'
import { ErrorMessage } from '@/components/ui/ErrorMessage'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { Shield, ArrowRight, KeyRound, Mail, Check } from 'lucide-react'
import { LanguageSwitcher } from '@/components/ui/LanguageSwitcher'

export function Login() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
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

  const oauthStep = searchParams.get('step')
  const oauthChallenge = searchParams.get('challenge') || ''
  const oauthSlug = searchParams.get('slug') || ''

  const [oauthProviders, setOauthProviders] = useState<OAuthProviderPublic[]>([])

  useEffect(() => {
    let active = true
    oauthApi.listPublicProviders()
      .then((list) => { if (active) setOauthProviders(list) })
      .catch(() => { /* kein Toast — public endpoint ist optional */ })
    return () => { active = false }
  }, [])

  useEffect(() => {
    const err = searchParams.get('error')
    if (err) {
      const translated = t(err, '')
      toast.error(translated || t('auth.loginFailed'))
      const next = new URLSearchParams(searchParams)
      next.delete('error')
      setSearchParams(next, { replace: true })
    }
  }, [searchParams, setSearchParams, t])

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

  if (oauthStep === 'oauth_2fa' && oauthChallenge && oauthSlug) {
    return (
      <LoginShell>
        <OAuth2FAStep
          slug={oauthSlug}
          challenge={oauthChallenge}
          onCancel={() => {
            const next = new URLSearchParams(searchParams)
            next.delete('step')
            next.delete('challenge')
            next.delete('slug')
            setSearchParams(next, { replace: true })
          }}
        />
      </LoginShell>
    )
  }

  return (
    <LoginShell>
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

            {oauthProviders.length > 0 && (
              <div className="mt-6 pt-6 border-t border-outline-variant/30 space-y-2">
                <p className="font-label-md text-xs text-on-surface-variant uppercase tracking-wider text-center">
                  {t('auth.or')}
                </p>
                <div className="grid gap-2">
                  {oauthProviders.map((p) => (
                    <a
                      key={p.slug}
                      href={apiUrl(`/oauth/${p.slug}/start?next=/&cb=${Date.now().toString(36)}`)}
                      className="msm-btn-secondary w-full py-2.5 inline-flex items-center justify-center gap-2"
                    >
                      <KeyRound className="w-4 h-4" />
                      {t('auth.signInWith', { provider: p.name })}
                    </a>
                  ))}
                </div>
              </div>
            )}

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
    </LoginShell>
  )
}

function LoginShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      <div className="absolute inset-0 msm-deep-grid opacity-50" />
      <div className="relative z-10 w-full max-w-md">
        <div className="flex justify-end mb-4">
          <LanguageSwitcher />
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

        {children}

        <VersionFooter />
      </div>
    </div>
  )
}

function OAuth2FAStep({ slug, challenge, onCancel }: { slug: string; challenge: string; onCancel: () => void }) {
  const { t } = useTranslation()
  const [otp, setOtp] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (otp.length !== 6) return
    setSubmitting(true)
    setError('')
    try {
      // redirect: 'manual' — wir werten den 302 als Erfolg; die Set-Cookie-Header
      // nimmt der Browser mit, danach machen wir eine Hartnavigation.
      const res = await fetch(apiUrl(`/oauth/${slug}/2fa`), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ challenge, otp_code: otp }),
        redirect: 'manual',
      })
      if (res.status === 0 || res.type === 'opaqueredirect' || (res.status >= 200 && res.status < 400)) {
        window.location.href = '/'
        return
      }
      const data = await res.json().catch(() => null)
      const detail = data?.detail
      const msg = typeof detail === 'string' ? t(detail, '') || detail : t('auth.loginFailed')
      setError(msg || t('auth.loginFailed'))
    } catch (err: any) {
      setError(err?.message || t('auth.loginFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="msm-card p-8">
      <div className="text-center mb-6">
        <div className="w-12 h-12 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto mb-4">
          <Shield className="w-6 h-6 text-secondary" />
        </div>
        <h2 className="font-headline text-headline-md text-primary mb-1">
          {t('auth.oauth2faTitle')}
        </h2>
        <p className="font-body-md text-sm text-on-surface-variant">
          {t('auth.oauth2faDescription', { provider: slug })}
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('auth.otpCode')}
          </label>
          <input
            type="text"
            inputMode="numeric"
            pattern="\d{6}"
            maxLength={6}
            value={otp}
            onChange={(e) => setOtp(e.target.value)}
            className="msm-input text-center text-2xl tracking-[0.5em] font-mono"
            placeholder="000000"
            required
            autoFocus
          />
        </div>

        <ErrorMessage message={error} className="text-sm" />

        <button
          type="submit"
          disabled={submitting || otp.length !== 6}
          className="msm-btn-primary w-full py-3 inline-flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {submitting ? (
            <span className="inline-flex items-center gap-2">
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              {t('common.loading')}
            </span>
          ) : (
            <>
              {t('auth.oauth2faSubmit')}
              <ArrowRight className="w-4 h-4" />
            </>
          )}
        </button>

        <button
          type="button"
          onClick={onCancel}
          className="w-full text-sm text-on-surface-variant hover:text-on-surface transition-colors"
        >
          {t('auth.goToLogin')}
        </button>
      </form>
    </div>
  )
}
