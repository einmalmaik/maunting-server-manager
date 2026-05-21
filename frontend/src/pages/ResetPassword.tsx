import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Shield, Check, X, ArrowRight } from 'lucide-react'

export function ResetPassword() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [status, setStatus] = useState<'idle' | 'submitting' | 'success' | 'error'>('idle')
  const [message, setMessage] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setMessage('')

    if (!token) {
      setStatus('error')
      setMessage(t('resetPassword.noToken'))
      return
    }

    if (password !== confirm) {
      setMessage(t('auth.passwordMismatch'))
      return
    }

    if (password.length < 8) {
      setMessage(t('auth.passwordTooShort'))
      return
    }

    setStatus('submitting')

    try {
      await api('/auth/reset-password', {
        method: 'POST',
        body: JSON.stringify({ token, new_password: password }),
      })
      setStatus('success')
      setMessage(t('resetPassword.success'))
    } catch (err: any) {
      setStatus('error')
      setMessage(err.message || t('resetPassword.error'))
    }
  }

  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      <div className="absolute inset-0 msm-deep-grid opacity-50" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[400px] bg-secondary/10 blur-[120px] rounded-full pointer-events-none" />

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
              {t('resetPassword.title')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant">
              {t('resetPassword.description')}
            </p>
          </div>

          {status === 'success' && (
            <div className="text-center py-6 space-y-4">
              <div className="w-16 h-16 rounded-full bg-status-success/10 border border-status-success/30 flex items-center justify-center mx-auto">
                <Check className="w-8 h-8 text-status-success" />
              </div>
              <p className="font-body-md text-base text-on-surface">{message}</p>
              <Link
                to="/login"
                className="msm-btn-primary w-full py-3 flex items-center justify-center gap-2"
              >
                {t('auth.goToLogin')}
                <ArrowRight className="w-4 h-4" />
              </Link>
            </div>
          )}

          {status === 'error' && (
            <div className="text-center py-6 space-y-4">
              <div className="w-16 h-16 rounded-full bg-status-error/10 border border-status-error/30 flex items-center justify-center mx-auto">
                <X className="w-8 h-8 text-status-error" />
              </div>
              <p className="font-body-md text-base text-status-error">{message}</p>
              <Link
                to="/forgot-password"
                className="msm-btn-secondary w-full py-3 flex items-center justify-center gap-2"
              >
                {t('forgotPassword.title')}
              </Link>
            </div>
          )}

          {(status === 'idle' || status === 'submitting') && (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('auth.password')}
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
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
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className="msm-input"
                  placeholder="••••••••"
                  required
                />
              </div>

              {message && (
                <div className="msm-alert-error text-sm">
                  {message}
                </div>
              )}

              <button
                type="submit"
                disabled={status === 'submitting'}
                className="msm-btn-primary w-full py-3 flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {status === 'submitting' ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                    {t('common.loading')}
                  </span>
                ) : (
                  <>
                    {t('resetPassword.confirm')}
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </form>
          )}
        </div>

        <p className="text-center font-mono-sm text-mono-sm text-on-surface-variant mt-6 opacity-60">
          Maunting Server Manager v1.0.0
        </p>
      </div>
    </div>
  )
}