import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Shield, ArrowRight, Check } from 'lucide-react'

export function Register() {
  const { t } = useTranslation()
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [form, setForm] = useState({ username: '', email: '', password: '', confirm: '' })

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
      await api('/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          email: form.email,
          password: form.password,
        }),
      })
      setSuccess(true)
    } catch (err: any) {
      setError(err.message || t('auth.registerFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  if (success) {
    return (
      <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
        <div className="absolute inset-0 msm-deep-grid opacity-50" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[400px] bg-secondary/10 blur-[120px] rounded-full pointer-events-none" />
        <div className="relative z-10 w-full max-w-md">
          <div className="msm-card p-8 text-center">
            <div className="w-16 h-16 rounded-full bg-status-success/10 border border-status-success/30 flex items-center justify-center mx-auto mb-6">
              <Check className="w-8 h-8 text-status-success" />
            </div>
            <h2 className="font-headline text-headline-md text-primary mb-2">
              {t('auth.registerSuccess')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant mb-6">
              {t('auth.verifyEmailHint')}
            </p>
            <Link
              to="/login"
              className="msm-btn-primary px-8 py-3 inline-flex items-center gap-2"
            >
              {t('auth.goToLogin')}
              <ArrowRight className="w-4 h-4" />
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      <div className="absolute inset-0 msm-deep-grid opacity-50" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[400px] bg-secondary/10 blur-[120px] rounded-full pointer-events-none" />
      <div className="absolute top-20 right-20 w-64 h-64 bg-cyan-glow blur-[80px] rounded-full pointer-events-none opacity-40" />

      <div className="relative z-10 w-full max-w-md">
        <div className="flex items-center justify-center gap-3 mb-8">
          <div className="w-10 h-10 rounded-md bg-primary flex items-center justify-center text-on-primary font-headline text-headline-md font-extrabold">
            M
          </div>
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
          <div className="text-center mb-6">
            <div className="w-12 h-12 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto mb-4">
              <Shield className="w-6 h-6 text-secondary" />
            </div>
            <h2 className="font-headline text-headline-md text-primary mb-1">
              {t('auth.register')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant">
              {t('auth.registerDescription')}
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
                minLength={3}
              />
            </div>

            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('auth.email')}
              </label>
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                className="msm-input"
                placeholder="admin@example.com"
                required
              />
            </div>

            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('auth.password')}
              </label>
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                className="msm-input"
                placeholder="••••••••"
                required
                minLength={8}
              />
            </div>

            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('auth.confirmPassword')}
              </label>
              <input
                type="password"
                value={form.confirm}
                onChange={(e) => setForm({ ...form, confirm: e.target.value })}
                className="msm-input"
                placeholder="••••••••"
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
                  {t('auth.createAccount')}
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          <div className="mt-6 text-center font-body-md text-sm">
            <Link to="/login" className="text-secondary hover:text-mint-accent transition-colors">
              {t('auth.hasAccount')}
            </Link>
          </div>
        </div>

        <p className="text-center font-mono-sm text-mono-sm text-on-surface-variant mt-6 opacity-60">
          Maunting Server Manager v1.0.0
        </p>
      </div>
    </div>
  )
}