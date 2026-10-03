import { useState, useEffect, useId } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { apiUrl } from '@/config/api'
import { useAuthStore } from '@/stores/authStore'
import { oauthApi, type OAuthProviderPublic } from '@/api/oauth'
import { toast } from '@/stores/toastStore'
import type { User, Zweitfaktor } from '@/types'
import { Logo } from '@/components/Logo'
import { VersionFooter } from '@/components/VersionFooter'
import { ErrorMessage } from '@/components/ui/ErrorMessage'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { CaptchaWidget, captchaSperrt, type CaptchaStatus } from '@/components/ui/CaptchaWidget'
import { Shield, ArrowRight, KeyRound, Mail, Check, Fingerprint } from 'lucide-react'
import { Button, buttonClasses, Input } from '@/Singra/UI'
import { Spinner } from '@/components/ui/Spinner'
import { sicheresZiel } from '@/lib/sicheresZiel'
import { passkeyBestaetigen, type PasskeyBestaetigungsOptionen } from '@/services/passkeyService'
export function Login() {
  const { t } = useTranslation()
  const feld = useId()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const { finishLogin } = useAuthStore()
  const [error, setError] = useState('')
  const [captchaToken, setCaptchaToken] = useState<string | null>(null)
  const [captchaStatus, setCaptchaStatus] = useState<CaptchaStatus>('loading')
  const [captchaResetKey, setCaptchaResetKey] = useState(0)
  const [form, setForm] = useState({ username: '', password: '', otp: '' })
  const [requires2FA, setRequires2FA] = useState(false)
  // Welche Faktoren gelten, der zuletzt genutzte vorn — der Server sagt es
  // nach dem Passwort. Angeboten werden nur diese, dazu der Backup-Code.
  const [methoden, setMethoden] = useState<Zweitfaktor[]>([])
  const [weg, setWeg] = useState<Anmeldeweg | null>(null)
  const [passkeyOptionen, setPasskeyOptionen] = useState<PasskeyBestaetigungsOptionen | null>(null)
  // Ersetzt im zweiten Schritt das Captcha-Token: das gilt nur einmal.
  const [zwischenschein, setZwischenschein] = useState<string | null>(null)
  // Solange die Sicherheitsabfrage nicht bestanden ist, bleiben Formular und
  // Social Login zu. Sonst war der Social Login der Weg drumherum. Im
  // 2FA-Schritt ist die Abfrage schon bestanden und das Widget ausgeblendet.
  const captchaBlockiert = !requires2FA && captchaSperrt(captchaStatus)
  const [submitting, setSubmitting] = useState(false)
  const [requiresVerification, setRequiresVerification] = useState(false)
  const [verifyEmail, setVerifyEmail] = useState('')
  const [verifyCode, setVerifyCode] = useState('')
  const [verifiedSuccess, setVerifiedSuccess] = useState(false)
  const [pendingVerifiedUser, setPendingVerifiedUser] = useState<User | null>(null)

  // ProtectedRoute legt die ursprünglich angefragte Seite in location.state ab.
  // Nach der Anmeldung geht es dorthin zurück statt immer auf das Dashboard.
  const redirectParam = searchParams.get('redirect')
  const gemerktesZiel = (location.state as { from?: string } | null)?.from || redirectParam
  const zielNachLogin = sicheresZiel(gemerktesZiel)


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
    if (captchaBlockiert) return
    setError('')
    setSubmitting(true)

    try {
      const res = await api<ZweiFaktorAntwort & { access_token: string; requires_verification: boolean; email: string }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          password: form.password,
          otp_code: form.otp || null,
          captcha_token: captchaToken,
          login_challenge: zwischenschein,
        }),
      })

      if (res.requires_verification) {
        setRequiresVerification(true)
        setVerifyEmail(res.email)
        setSubmitting(false)
        return
      }

      if (res.requires_2fa) {
        zweiterFaktorVerlangt(res)
        setSubmitting(false)
        return
      }

      const user = await api<User>('/auth/me')
      await finishLogin(user)
      navigate(zielNachLogin, { replace: true })
    } catch (err: any) {
      setError(err.message || t('auth.loginFailed'))
      setSubmitting(false)
      setCaptchaToken(null)
      setCaptchaResetKey((k) => k + 1)
      if (requires2FA && err?.status === 403) vonVorn()
    }
  }

  /**
   * Der Zwischenschein ist verbraucht (drei Fehlversuche) oder abgelaufen: der
   * nächste Versuch braucht wieder Sicherheitsabfrage und Passwort. Die Meldung
   * vom Server bleibt stehen.
   */
  const vonVorn = () => {
    setRequires2FA(false)
    setMethoden([])
    setWeg(null)
    setPasskeyOptionen(null)
    setZwischenschein(null)
    setForm((f) => ({ ...f, otp: '' }))
    setCaptchaToken(null)
    setCaptchaResetKey((k) => k + 1)
  }

  const zweiterFaktorVerlangt = (res: ZweiFaktorAntwort) => {
    const m = res.two_factor_methods ?? []
    setRequires2FA(true)
    setMethoden(m)
    // Ein neuer Versuch (etwa nach abgebrochenem Passkey) bleibt beim gewählten Weg.
    setWeg((vorher) => vorher ?? m[0] ?? 'backup')
    setPasskeyOptionen(res.passkey_options ?? null)
    if (res.login_challenge) setZwischenschein(res.login_challenge)
  }

  /**
   * Der Passkey unterschreibt die Challenge aus dem ersten Schritt; der Server
   * prüft die Unterschrift. Scheitert es (abgelaufen, abgebrochen), holt ein
   * Aufruf ohne Nachweis eine frische Challenge für den nächsten Versuch.
   */
  const handlePasskeyLogin = async () => {
    setError('')
    if (!passkeyOptionen) {
      setError(t('auth.passkeyNotHere'))
      return
    }
    setSubmitting(true)
    try {
      const nachweis = await passkeyBestaetigen(passkeyOptionen)
      await api<{ access_token: string }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          password: form.password,
          captcha_token: captchaToken,
          login_challenge: zwischenschein,
          passkey: nachweis,
        }),
      })
      const user = await api<User>('/auth/me')
      await finishLogin(user)
      navigate(zielNachLogin, { replace: true })
    } catch (err: any) {
      setError(err.message || t('auth.loginFailed'))
      if (err?.status === 403) {
        vonVorn()
        return
      }
      try {
        const frisch = await api<ZweiFaktorAntwort>('/auth/login', {
          method: 'POST',
          body: JSON.stringify({
            username: form.username,
            password: form.password,
            captcha_token: captchaToken,
            login_challenge: zwischenschein,
          }),
        })
        if (frisch.requires_2fa) zweiterFaktorVerlangt(frisch)
      } catch (nochmal: any) {
        // Der Fehler oben steht schon da.
        if (nochmal?.status === 403) vonVorn()
      }
    } finally {
      setSubmitting(false)
    }
  }

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const res = await api<ZweiFaktorAntwort>('/auth/login-verify', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          password: form.password,
          code: verifyCode,
          otp_code: form.otp || null,
        }),
      })
      if (res.requires_2fa) {
        zweiterFaktorVerlangt(res)
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
      setError(err.message || t('auth.resendFailed'))
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
            <Button size="lg"
              onClick={() => {
                if (!pendingVerifiedUser) return
                void finishLogin(pendingVerifiedUser).then(() => navigate(zielNachLogin, { replace: true }))
              }}
              className="inline-flex items-center gap-2"
            >
              {t('auth.continue')}
              <ArrowRight className="w-4 h-4" />
            </Button>
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
                <label htmlFor={`${feld}-code`} className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('auth.verificationCode')}
                </label>
                <Input
                  id={`${feld}-code`}
                  type="text"
                  inputMode="numeric"
                  pattern="\d{6}"
                  maxLength={6}
                  value={verifyCode}
                  onChange={(e) => setVerifyCode(e.target.value)}
                  className="text-center text-2xl tracking-[0.5em] font-mono"
                  placeholder="000000"
                  required
                />
              </div>

              <ErrorMessage message={error} className="text-sm" />

              <Button size="lg"
                type="submit"
                disabled={submitting || verifyCode.length !== 6}
                className="w-full disabled:opacity-50"
              >
                {submitting ? (
                  <span className="inline-flex items-center gap-2">
                    <Spinner />
                    {t('common.loading')}
                  </span>
                ) : (
                  t('auth.verifyNow')
                )}
              </Button>

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
                <label htmlFor={`${feld}-name`} className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('auth.username')}
                </label>
                <Input
                  id={`${feld}-name`}
                  type="text"
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
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
                  {weg === 'passkey' && (
                    <Button
                      type="button"
                      onClick={() => void handlePasskeyLogin()}
                      disabled={submitting}
                      className="w-full flex items-center justify-center gap-2"
                    >
                      <Fingerprint className="w-5 h-5" />
                      {t('auth.loginWithPasskey')}
                    </Button>
                  )}
                  {weg !== 'passkey' && (
                  <div>
                    <label htmlFor={`${feld}-otp`} className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                      {weg === 'backup'
                        ? t('auth.backupCode')
                        : t('auth.otpCode')}
                    </label>
                    <Input
                      id={`${feld}-otp`}
                      type="text"
                      value={form.otp}
                      onChange={(e) => setForm({ ...form, otp: e.target.value })}
                      placeholder={weg === 'backup' ? 'XXXX-XXXX' : '000000'}
                      required
                      maxLength={weg === 'backup' ? 12 : 6}
                    />
                  </div>
                  )}
                  <AndereWege
                    methoden={methoden}
                    aktiv={weg}
                    disabled={submitting}
                    waehlen={(neu) => {
                      setWeg(neu)
                      setForm({ ...form, otp: '' })
                      setError('')
                    }}
                  />
                </>
              )}

              {!requires2FA && (
                <CaptchaWidget onVerify={setCaptchaToken} onStatusChange={setCaptchaStatus} resetKey={captchaResetKey} />
              )}

              <ErrorMessage message={error} className="text-sm" />

              {!(requires2FA && weg === 'passkey') && (
              <Button size="lg"
                type="submit"
                disabled={submitting || captchaBlockiert}
                className="w-full flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {submitting ? (
                  <span className="inline-flex items-center gap-2">
                    <Spinner />
                    {t('common.loading')}
                  </span>
                ) : (
                  <>
                    {requires2FA ? t('auth.verify2FA') : t('auth.signIn')}
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </Button>
              )}
            </form>

            {oauthProviders.length > 0 && (
              <div className="mt-6 pt-6 border-t border-outline-variant/30 space-y-2">
                <p className="font-label-md text-xs text-on-surface-variant uppercase tracking-wider text-center">
                  {t('auth.or')}
                </p>
                <div className="grid gap-2">
                  {oauthProviders.map((p) =>
                    captchaBlockiert ? (
                      <Button key={p.slug} variant="secondary" size="lg" className="w-full" disabled>
                        <KeyRound className="w-4 h-4" />
                        {t('auth.signInWith', { provider: p.name })}
                      </Button>
                    ) : (
                      <a
                        key={p.slug}
                        href={apiUrl(`/oauth/${p.slug}/start?next=/&cb=${Date.now().toString(36)}`)}
                        className={buttonClasses('secondary', 'lg', 'w-full')}
                      >
                        <KeyRound className="w-4 h-4" />
                        {t('auth.signInWith', { provider: p.name })}
                      </a>
                    ),
                  )}
                </div>
              </div>
            )}

            <div className="mt-6 pt-6 border-t border-outline-variant/30 flex justify-between font-body-md text-sm">
              <Link
                to={gemerktesZiel ? `/register?redirect=${encodeURIComponent(gemerktesZiel)}` : '/register'}
                state={{ from: gemerktesZiel }}
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

        <div className="flex items-center justify-center gap-3 mb-8">
          <Logo size="md" />
          <div>
            <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">
              MSM
            </h1>
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
  const codeFeld = useId()
  const [otp, setOtp] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  // Die Faktoren des Kontos, der zuletzt genutzte vorn. Kein Faktor zur Hand:
  // der Backup-Code gilt auch hier.
  const [methoden, setMethoden] = useState<Zweitfaktor[] | null>(null)
  const [weg, setWeg] = useState<Anmeldeweg | null>(null)
  // Scheitert die Abfrage des Faktors, wird nicht geraten: bis 09/2026 galt dann
  // TOTP, und Passkey-Konten sahen nur ein Codefeld ohne jeden anderen Weg.
  const [ladeFehler, setLadeFehler] = useState('')

  /** Welche Faktoren gelten — und bei Passkeys eine frische Challenge. */
  const methodeLaden = async (): Promise<{ optionen: PasskeyBestaetigungsOptionen | null; hinweis: string }> => {
    let res: Response
    try {
      res = await fetch(apiUrl(`/oauth/${slug}/2fa/methode`), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ challenge }),
      })
    } catch {
      throw new Error(t('auth.oauth2faMethodFailed'))
    }
    if (!res.ok) {
      throw new Error(t(res.status === 429 ? 'auth.oauth2faMethodRateLimited' : 'auth.oauth2faMethodFailed'))
    }
    const daten = (await res.json()) as {
      methoden?: Zweitfaktor[]
      passkey_options?: PasskeyBestaetigungsOptionen
      hinweis?: string
    }
    const m = daten.methoden ?? []
    setMethoden(m)
    setWeg((vorher) => vorher ?? m[0] ?? 'backup')
    return { optionen: daten.passkey_options ?? null, hinweis: daten.hinweis ?? '' }
  }

  const faktorAbfragen = () => {
    setLadeFehler('')
    methodeLaden().catch((err: Error) => setLadeFehler(err.message))
  }

  useEffect(() => {
    faktorAbfragen()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, challenge])

  const wegWaehlen = (neu: Anmeldeweg) => {
    setWeg(neu)
    setOtp('')
    setError('')
  }

  const handlePasskeyOAuth = async () => {
    setError('')
    setSubmitting(true)
    try {
      // Die Challenge wird erst beim Klick geholt: sie gilt fünf Minuten.
      const { optionen, hinweis } = await methodeLaden()
      if (!optionen) {
        setError(hinweis || t('auth.passkeyNotHere'))
        return
      }
      const nachweis = await passkeyBestaetigen(optionen)
      const res = await fetch(apiUrl(`/oauth/${slug}/2fa`), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ challenge, passkey: nachweis }),
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (otp.trim().length < 6) return
    setSubmitting(true)
    setError('')
    try {
      // redirect: 'manual' — wir werten den 302 als Erfolg; die Set-Cookie-Header
      // nimmt der Browser mit, danach machen wir eine Hartnavigation.
      const res = await fetch(apiUrl(`/oauth/${slug}/2fa`), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ challenge, otp_code: otp.trim() }),
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

      {methoden === null && !ladeFehler && (
        <div className="flex justify-center py-4">
          <Spinner />
        </div>
      )}

      {methoden === null && ladeFehler && (
        <div className="space-y-4">
          <ErrorMessage message={ladeFehler} className="text-sm" />
          <Button type="button" size="lg" onClick={faktorAbfragen} className="w-full">
            {t('common.retry')}
          </Button>
          <button
            type="button"
            onClick={onCancel}
            className="w-full text-sm text-on-surface-variant hover:text-on-surface transition-colors"
          >
            {t('auth.goToLogin')}
          </button>
        </div>
      )}

      {methoden !== null && weg === 'passkey' && (
        <div className="space-y-4">
          <Button
            type="button"
            size="lg"
            onClick={() => void handlePasskeyOAuth()}
            disabled={submitting}
            className="w-full flex items-center justify-center gap-2"
          >
            <Fingerprint className="w-5 h-5" />
            {t('auth.loginWithPasskey')}
          </Button>
          <ErrorMessage message={error} className="text-sm" />
          <AndereWege methoden={methoden} aktiv={weg} waehlen={wegWaehlen} disabled={submitting} />
          <button
            type="button"
            onClick={onCancel}
            className="w-full text-sm text-on-surface-variant hover:text-on-surface transition-colors"
          >
            {t('auth.goToLogin')}
          </button>
        </div>
      )}

      {methoden !== null && (weg === 'totp' || weg === 'backup') && (
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor={codeFeld} className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {weg === 'backup' ? t('auth.backupCode') : t('auth.otpCode')}
          </label>
          <Input
            id={codeFeld}
            type="text"
            inputMode={weg === 'backup' ? 'text' : 'numeric'}
            maxLength={weg === 'backup' ? 12 : 6}
            value={otp}
            onChange={(e) => setOtp(e.target.value)}
            className="text-center text-2xl tracking-[0.5em] font-mono"
            placeholder={weg === 'backup' ? 'XXXX-XXXX' : '000000'}
            required
            autoFocus
          />
        </div>

        <AndereWege methoden={methoden} aktiv={weg} waehlen={wegWaehlen} disabled={submitting} />

        <ErrorMessage message={error} className="text-sm" />

        <Button size="lg"
          type="submit"
          disabled={submitting || otp.trim().length < 6}
          className="w-full inline-flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {submitting ? (
            <span className="inline-flex items-center gap-2">
              <Spinner />
              {t('common.loading')}
            </span>
          ) : (
            <>
              {t('auth.oauth2faSubmit')}
              <ArrowRight className="w-4 h-4" />
            </>
          )}
        </Button>

        <button
          type="button"
          onClick={onCancel}
          className="w-full text-sm text-on-surface-variant hover:text-on-surface transition-colors"
        >
          {t('auth.goToLogin')}
        </button>
      </form>
      )}
    </div>
  )
}

/** Ein Weg im zweiten Schritt: ein Faktor des Kontos oder der Backup-Code. */
type Anmeldeweg = Zweitfaktor | 'backup'

/**
 * „Andere Methode“: die übrigen Faktoren des Kontos und der Backup-Code, je
 * ein Klick. Ein Konto mit Passkey am PC und App am Handy kommt so an jedem
 * Gerät hinein.
 */
function AndereWege({
  methoden,
  aktiv,
  waehlen,
  disabled,
}: {
  methoden: Zweitfaktor[]
  aktiv: Anmeldeweg | null
  waehlen: (weg: Anmeldeweg) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const namen: Record<Anmeldeweg, string> = {
    passkey: t('auth.zweitfaktor.wegPasskey'),
    totp: t('auth.zweitfaktor.wegTotp'),
    backup: t('auth.useBackupCode'),
  }
  const andere = ([...methoden, 'backup'] as Anmeldeweg[]).filter((w) => w !== aktiv)
  return (
    <div className="space-y-1">
      <p className="text-xs text-on-surface-variant">{t('auth.zweitfaktor.andereMethode')}</p>
      <div className="flex flex-wrap gap-1">
        {andere.map((w) => (
          <Button key={w} type="button" variant="ghost" size="sm" disabled={disabled} onClick={() => waehlen(w)}>
            {w === 'passkey' ? <Fingerprint className="h-3.5 w-3.5" aria-hidden="true" /> : <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />}
            {namen[w]}
          </Button>
        ))}
      </div>
    </div>
  )
}

/** Antwort von `/auth/login` und `/auth/login-verify`, soweit der zweite Faktor sie braucht. */
interface ZweiFaktorAntwort {
  requires_2fa: boolean
  two_factor_methods?: Zweitfaktor[]
  passkey_options?: PasskeyBestaetigungsOptionen | null
  login_challenge?: string
}
