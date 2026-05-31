import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Logo } from '@/components/Logo'
import { VersionFooter } from '@/components/VersionFooter'
import { ErrorMessage } from '@/components/ui/ErrorMessage'
import { Shield, Mail, ArrowRight, ArrowLeft } from 'lucide-react'

export function ForgotPassword() {
  const { t } = useTranslation()
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<'idle' | 'submitting' | 'sent'>('idle')
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setStatus('submitting')

    try {
      await api('/auth/forgot-password', {
        method: 'POST',
        body: JSON.stringify({ email }),
      })
      setStatus('sent')
    } catch (err: any) {
      setError(err.message || t('forgotPassword.error'))
      setStatus('idle')
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
              {t('forgotPassword.title')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant">
              {t('forgotPassword.description')}
            </p>
          </div>

          {status === 'sent' ? (
            <div className="text-center py-6 space-y-4">
              <div className="w-16 h-16 rounded-full bg-status-success/10 border border-status-success/30 flex items-center justify-center mx-auto">
                <Mail className="w-8 h-8 text-status-success" />
              </div>
              <p className="font-body-md text-base text-on-surface">{t('forgotPassword.sent')}</p>
              <Link
                to="/login"
                className="msm-btn-primary w-full py-3 flex items-center justify-center gap-2"
              >
                {t('auth.goToLogin')}
                <ArrowRight className="w-4 h-4" />
              </Link>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('auth.email')}
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="msm-input"
                  placeholder="admin@example.com"
                  required
                />
              </div>

              <ErrorMessage message={error} className="text-sm" />

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
                    {t('forgotPassword.sendLink')}
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </form>
          )}

          <div className="mt-6 text-center">
            <Link
              to="/login"
              className="font-body-md text-sm text-secondary hover:text-mint-accent transition-colors inline-flex items-center gap-1.5"
            >
              <ArrowLeft className="w-4 h-4" />
              {t('auth.hasAccount')}
            </Link>
          </div>
        </div>

        <VersionFooter />
      </div>
    </div>
  )
}
