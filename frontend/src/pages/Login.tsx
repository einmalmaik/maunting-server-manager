import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import type { User } from '@/types'
import { Shield, ArrowRight, Globe } from 'lucide-react'

export function Login() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { setUser, setAuthenticated } = useAuthStore()
  const [error, setError] = useState('')
  const [form, setForm] = useState({ username: '', password: '', otp: '' })
  const [requires2FA, setRequires2FA] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)

    try {
      const res = await api<{ access_token: string; requires_2fa: boolean }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          password: form.password,
          otp_code: form.otp || null,
        }),
      })

      if (res.requires_2fa) {
        setRequires2FA(true)
        setSubmitting(false)
        return
      }

      // Token wird vom Backend als httpOnly Cookie gesetzt
      const user = await api<User>('/auth/me')
      setUser(user)
      setAuthenticated(true)
      navigate('/')
    } catch (err: any) {
      setError(err.message || t('auth.loginFailed'))
      setSubmitting(false)
    }
  }

  const toggleLang = () => {
    const next = i18n.language === 'de' ? 'en' : 'de'
    i18n.changeLanguage(next)
  }

  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      {/* Deep Grid Background */}
      <div className="absolute inset-0 msm-deep-grid opacity-50" />

      {/* Ambient Glow */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[400px] bg-secondary/10 blur-[120px] rounded-full pointer-events-none" />
      <div className="absolute top-20 right-20 w-64 h-64 bg-cyan-glow blur-[80px] rounded-full pointer-events-none opacity-40" />

      <div className="relative z-10 w-full max-w-md">
        {/* Language Toggle */}
        <div className="flex justify-end mb-4">
          <button
            onClick={toggleLang}
            className="flex items-center gap-1.5 font-label-md text-xs text-on-surface-variant hover:text-primary transition-colors"
          >
            <Globe className="w-3.5 h-3.5" />
            {i18n.language.toUpperCase()}
          </button>
        </div>

        {/* Brand */}
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

        {/* Login Card */}
        <div className="msm-card p-8">
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
                disabled={requires2FA}
              />
            </div>

            {requires2FA && (
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('auth.otpCode')}
                </label>
                <input
                  type="text"
                  value={form.otp}
                  onChange={(e) => setForm({ ...form, otp: e.target.value })}
                  className="msm-input"
                  placeholder="000000"
                  required
                  pattern="\d{6}"
                  maxLength={6}
                />
              </div>
            )}

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
        </div>

        <p className="text-center font-mono-sm text-mono-sm text-on-surface-variant mt-6 opacity-60">
          Maunting Server Manager v1.0.0
        </p>
      </div>
    </div>
  )
}