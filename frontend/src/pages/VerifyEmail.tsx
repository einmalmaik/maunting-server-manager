import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { VersionFooter } from '@/components/VersionFooter'
import { Shield, Check, X, ArrowRight } from 'lucide-react'

export function VerifyEmail() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')

  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('error')
      setMessage(t('verifyEmail.noToken'))
      return
    }

    api(`/auth/verify-email?token=${encodeURIComponent(token)}`)
      .then(() => {
        setStatus('success')
        setMessage(t('verifyEmail.success'))
      })
      .catch((err: Error) => {
        setStatus('error')
        setMessage(err.message || t('verifyEmail.error'))
      })
  }, [token, t])

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
              {t('verifyEmail.title')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant">
              {status === 'loading' ? t('verifyEmail.loading') : ''}
            </p>
          </div>

          {status === 'loading' && (
            <div className="flex justify-center py-8">
              <span className="w-8 h-8 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
            </div>
          )}

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
                to="/register"
                className="msm-btn-secondary w-full py-3 flex items-center justify-center gap-2"
              >
                {t('auth.register')}
              </Link>
            </div>
          )}
        </div>

        <VersionFooter />
      </div>
    </div>
  )
}