import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Shield, Server, ChevronRight, Check, Mail } from 'lucide-react'
import { Logo } from '@/components/Logo'
import { VersionFooter } from '@/components/VersionFooter'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { api } from '@/api/client'

interface SetupWizardProps {
  onComplete: () => void
  emailConfigured: boolean
}

export function SetupWizard({ onComplete, emailConfigured }: SetupWizardProps) {
  const { t } = useTranslation()
  const [step, setStep] = useState(1)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [form, setForm] = useState({
    username: '',
    email: '',
    password: '',
    confirm: '',
    code: '',
    fromAddress: '',
    resendApiKey: '',
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (form.password !== form.confirm) {
      setError(t('auth.passwordMismatch'))
      return
    }
    if (form.password.length < 8) {
      setError(t('auth.passwordTooShort'))
      return
    }

    setSubmitting(true)
    try {
      const emailConfig = emailConfigured
        ? undefined
        : {
            provider: 'resend',
            from_address: form.fromAddress,
            resend_api_key: form.resendApiKey,
          }

      const res = await api<{ requires_verification: boolean; message: string }>('/auth/setup', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          email: form.email,
          password: form.password,
          ...(emailConfig ? { email_config: emailConfig } : {}),
        }),
      })

      if (res.requires_verification) {
        setStep(3)
      } else {
        setStep(4)
        setTimeout(onComplete, 2000)
      }
    } catch (err: any) {
      setError(err.message || t('setup.error'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await api('/auth/setup-verify', {
        method: 'POST',
        body: JSON.stringify({
          email: form.email,
          code: form.code,
        }),
      })
      setStep(4)
      setTimeout(onComplete, 2000)
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
      await api('/auth/setup-resend', {
        method: 'POST',
        body: JSON.stringify({ email: form.email }),
      })
    } catch (err: any) {
      setError(err.message || t('setup.resendFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  const steps = [
    { num: 1, label: 'Willkommen' },
    { num: 2, label: 'Owner erstellen' },
    { num: 3, label: 'Verifizieren' },
    { num: 4, label: 'Fertig' },
  ]

  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      {/* Deep Grid Background */}
      <div className="absolute inset-0 msm-deep-grid opacity-50" />

      {/* Ambient Glow */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[400px] bg-secondary/10 blur-[120px] rounded-full pointer-events-none" />
      <div className="absolute top-20 right-20 w-64 h-64 bg-cyan-glow blur-[80px] rounded-full pointer-events-none opacity-40" />

      <div className="relative z-10 w-full max-w-lg">
        {/* Brand Header */}
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

        {/* Step Indicator */}
        <div className="flex items-center justify-center gap-2 mb-8">
          {steps.map((s, i) => (
            <div key={s.num} className="flex items-center gap-2">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold transition-colors duration-300 ${
                  step >= s.num
                    ? 'bg-secondary-container text-on-secondary-container'
                    : 'bg-surface-container-high text-on-surface-variant'
                }`}
              >
                {step > s.num ? <Check className="w-4 h-4" /> : s.num}
              </div>
              <span
                className={`font-label-md text-xs uppercase tracking-wider hidden sm:inline ${
                  step >= s.num ? 'text-on-surface' : 'text-on-surface-variant'
                }`}
              >
                {s.label}
              </span>
              {i < steps.length - 1 && (
                <div
                  className={`w-8 h-px transition-colors duration-300 ${
                    step > s.num ? 'bg-secondary-container' : 'bg-outline-variant'
                  }`}
                />
              )}
            </div>
          ))}
        </div>

        {/* Main Panel */}
        <div className="msm-card overflow-hidden">
          {/* Step 1: Welcome */}
          {step === 1 && (
            <div className="p-8 text-center">
              <div className="w-16 h-16 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto mb-6">
                <Server className="w-8 h-8 text-secondary" />
              </div>
              <h2 className="font-headline text-headline-md text-primary mb-3">
                {t('setup.welcome')}
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant mb-8 max-w-sm mx-auto">
                {t('setup.welcomeDesc')}
              </p>
              <button
                onClick={() => setStep(2)}
                className="msm-btn-primary px-8 py-3 inline-flex items-center gap-2"
              >
                {t('setup.start')}
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Step 2: Create Owner */}
          {step === 2 && (
            <div className="p-8">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
                  <Shield className="w-5 h-5 text-secondary" />
                </div>
                <div>
                  <h2 className="font-headline text-headline-md text-primary">
                    {t('setup.title')}
                  </h2>
                  <p className="font-body-md text-sm text-on-surface-variant">
                    {t('setup.description')}
                  </p>
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label htmlFor="setup-username" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('auth.username', 'Benutzername')}
                  </label>
                  <input
                    type="text"
                    id="setup-username"
                    value={form.username}
                    onChange={(e) => setForm({ ...form, username: e.target.value })}
                    className="msm-input"
                    placeholder="admin"
                    required
                    minLength={3}
                  />
                </div>

                <div>
                  <label htmlFor="setup-owner-email" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('auth.email', 'E-Mail')}
                  </label>
                  <input
                    type="email"
                    id="setup-owner-email"
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                    className="msm-input"
                    placeholder="admin@example.com"
                    required
                  />
                </div>

                <PasswordInput
                  id="setup-owner-password"
                  label={t('auth.password', 'Passwort') || 'Passwort'}
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  placeholder="••••••••"
                  required
                  minLength={8}
                />

                <PasswordInput
                  id="setup-owner-password-confirm"
                  label={t('auth.confirmPassword', 'Passwort bestätigen') || 'Passwort bestätigen'}
                  value={form.confirm}
                  onChange={(e) => setForm({ ...form, confirm: e.target.value })}
                  placeholder="••••••••"
                  required
                />

                {!emailConfigured && (
                  <fieldset className="rounded-lg border border-outline-variant bg-surface-container-low p-4 space-y-4">
                    <legend className="px-1 font-label-md text-label-md text-on-surface uppercase tracking-wider">
                      {t('setup.emailDelivery')}
                    </legend>
                    <p className="font-body-md text-sm text-on-surface-variant">
                      {t('setup.emailDeliveryDesc')}
                    </p>

                    <div className="rounded-md border border-secondary/30 bg-secondary/10 px-3 py-2 text-sm text-on-surface">
                      {t('setup.resendRecommended')}
                    </div>

                    <div>
                      <label htmlFor="setup-from-address" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                        {t('setup.fromAddress')}
                      </label>
                      <input
                        id="setup-from-address"
                        type="email"
                        value={form.fromAddress}
                        onChange={(e) => setForm({ ...form, fromAddress: e.target.value })}
                        className="msm-input"
                        placeholder="panel@example.com"
                        autoComplete="email"
                        required
                      />
                    </div>

                    <PasswordInput
                      id="setup-resend-api-key"
                      label={t('setup.resendApiKey')}
                      value={form.resendApiKey}
                      onChange={(e) => setForm({ ...form, resendApiKey: e.target.value })}
                      placeholder="re_..."
                      autoComplete="off"
                      required
                    />
                    <p className="font-body-md text-xs text-on-surface-variant">
                      {t('setup.smtpAfterLogin')}
                    </p>
                  </fieldset>
                )}

                {error && (
                  <div className="msm-alert-error">
                    {error}
                  </div>
                )}

                <div className="flex gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setStep(1)}
                    className="msm-btn-secondary flex-1 py-3"
                  >
                    {t('common.back')}
                  </button>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="msm-btn-primary flex-1 py-3 disabled:opacity-50"
                  >
                    {submitting ? (
                      <span className="inline-flex items-center gap-2">
                        <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                        {t('common.loading')}
                      </span>
                    ) : (
                      t('setup.createOwner')
                    )}
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* Step 3: Email Verification Code */}
          {step === 3 && (
            <div className="p-8 text-center">
              <div className="w-16 h-16 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto mb-6">
                <Mail className="w-8 h-8 text-secondary" />
              </div>
              <h2 className="font-headline text-headline-md text-primary mb-3">
                {t('setup.verifyEmail')}
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant mb-2 max-w-sm mx-auto">
                {t('setup.verifyEmailDesc', { email: form.email })}
              </p>
              <p className="font-mono-sm text-mono-sm text-on-surface-variant mb-8">
                {t('setup.codeExpires')}
              </p>

              <form onSubmit={handleVerify} className="space-y-4 max-w-xs mx-auto">
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('setup.verificationCode')}
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="\d{6}"
                    maxLength={6}
                    value={form.code}
                    onChange={(e) => setForm({ ...form, code: e.target.value })}
                    className="msm-input text-center text-2xl tracking-[0.5em] font-mono"
                    placeholder="000000"
                    required
                  />
                </div>

                {error && (
                  <div className="msm-alert-error text-sm">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={submitting || form.code.length !== 6}
                  className="msm-btn-primary w-full py-3 disabled:opacity-50"
                >
                  {submitting ? (
                    <span className="inline-flex items-center gap-2">
                      <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                      {t('common.loading')}
                    </span>
                  ) : (
                    t('setup.verify')
                  )}
                </button>

                <button
                  type="button"
                  onClick={handleResendCode}
                  disabled={submitting}
                  className="text-sm text-secondary hover:text-mint-accent transition-colors disabled:opacity-50"
                >
                  {t('setup.resendCode')}
                </button>
              </form>
            </div>
          )}

          {/* Step 4: Success */}
          {step === 4 && (
            <div className="p-8 text-center">
              <div className="w-16 h-16 rounded-full bg-status-success/10 border border-status-success/30 flex items-center justify-center mx-auto mb-6">
                <Check className="w-8 h-8 text-status-success" />
              </div>
              <h2 className="font-headline text-headline-md text-primary mb-3">
                {t('setup.success')}
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant mb-2">
                {t('setup.successDesc')}
              </p>
              <p className="font-mono-sm text-mono-sm text-on-surface-variant">
                {t('setup.redirecting')}
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <VersionFooter />
      </div>
    </div>
  )
}
