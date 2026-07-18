import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Logo } from '@/components/Logo'
import { VersionFooter } from '@/components/VersionFooter'
import { ErrorMessage } from '@/components/ui/ErrorMessage'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { CaptchaWidget } from '@/components/ui/CaptchaWidget'
import { Shield, Check, X, ArrowRight } from 'lucide-react'

export function ResetPassword() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [captchaToken, setCaptchaToken] = useState<string | null>(null)
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
        body: JSON.stringify({ token, new_password: password, captcha_token: captchaToken }),
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

      <div className="relative z-10 w-full max-w-md">
        <div className="flex items-center justify-center gap-3 mb-8">
          <Logo size="md" />
          <div>
            <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">
              MauntingStudios
            </h1>
            <p className="font-mono-sm text-mono-sm text-on-surface-variant">
              Server Manager
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
              <PasswordInput
                label={t('auth.password') || 'Passwort'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                minLength={8}
              />

              <PasswordInput
                label={t('auth.confirmPassword') || 'Passwort bestätigen'}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="••••••••"
                required
              />

              <CaptchaWidget onVerify={setCaptchaToken} />

              <ErrorMessage message={message} className="text-sm" />

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

        <VersionFooter />
      </div>
    </div>
  )
}
