import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { api } from '@/api/client'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { KeyRound, Mail, Save } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { passkeyNachweis } from '@/services/passkeyService'

import { Spinner } from '@/components/ui/Spinner'
/**
 * Tab: Passwort aendern.
 * Validiert lokal (Laenge, Match), ruft /auth/change-password,
 * beruecksichtigt den eingerichteten zweiten Faktor: TOTP-Feld oder Passkey-Abfrage
 * beim Speichern — nie beides.
 * Konten ohne Passwort (Social Login) bekommen nur einen Link an ihre E-Mail:
 * ein angemeldetes Token allein setzt kein Passwort.
 */
export function PasswordTab() {
  const { t } = useTranslation()
  const { user } = useAuthStore()
  const hasPassword = user?.has_password ?? true
  const methode = user?.two_factor_enabled ? (user.two_factor_method ?? 'totp') : null
  const [form, setForm] = useState({ current: '', new: '', confirm: '', otp: '' })
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSuccess('')

    if (form.new !== form.confirm) {
      setError(t('profile.passwordMismatch'))
      return
    }
    if (form.new.length < 8) {
      setError(t('auth.passwordTooShort'))
      return
    }

    setSubmitting(true)
    try {
      await api('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: form.current,
          new_password: form.new,
          otp_code: methode === 'totp' ? form.otp : null,
          passkey: methode === 'passkey' ? await passkeyNachweis('password_change') : null,
        }),
      })
      setSuccess(t('profile.passwordChanged'))
      setForm({ current: '', new: '', confirm: '', otp: '' })
      setTimeout(() => setSuccess(''), 3000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleSendLink = async () => {
    setError('')
    setSuccess('')
    setSubmitting(true)
    try {
      await api('/auth/set-password', { method: 'POST' })
      setSuccess(t('profile.setPasswordLinkSent', { email: user?.email ?? '' }))
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (!hasPassword) {
    return (
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <KeyRound className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-title-lg font-semibold text-on-surface">{t('profile.setPassword')}</h2>
        </div>
        <p className="text-sm text-on-surface-variant mb-4">{t('profile.setPasswordInfo')}</p>
        {error && <div className="msm-alert-error text-sm mb-4">{error}</div>}
        {success && <div className="msm-alert-success text-sm mb-4">{success}</div>}
        <div className="flex justify-end">
          <Button
            type="button"
            onClick={handleSendLink}
            disabled={submitting}
            className="inline-flex items-center gap-2 disabled:opacity-50"
          >
            {submitting ? <Spinner /> : <Mail className="w-4 h-4" />}
            {t('profile.setPasswordSendLink')}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="msm-card p-6">
      <div className="flex items-center gap-2 mb-6">
        <KeyRound className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h2 className="font-headline text-title-lg font-semibold text-on-surface">{t('profile.changePassword')}</h2>
      </div>

      {error && <div className="msm-alert-error text-sm mb-4">{error}</div>}
      {success && <div className="msm-alert-success text-sm mb-4">{success}</div>}

      <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="md:col-span-2">
          <label htmlFor="current_password" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('profile.currentPassword')}
          </label>
          <PasswordInput
            id="current_password"
            value={form.current}
            onChange={(e) => setForm({ ...form, current: e.target.value })}
            required
          />
        </div>
        <div>
          <label htmlFor="new_password" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('profile.newPassword')}
          </label>
          <PasswordInput
            id="new_password"
            value={form.new}
            onChange={(e) => setForm({ ...form, new: e.target.value })}
            required
            minLength={8}
          />
        </div>
        <div>
          <label htmlFor="confirm_password" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('profile.confirmPassword')}
          </label>
          <PasswordInput
            id="confirm_password"
            value={form.confirm}
            onChange={(e) => setForm({ ...form, confirm: e.target.value })}
            required
            minLength={8}
          />
        </div>
        {methode === 'totp' && (
          <div className="md:col-span-2">
            <label htmlFor="otp_code" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('auth.otpCode')}
            </label>
            <input
              id="otp_code"
              type="text"
              inputMode="numeric"
              pattern="\d{6}"
              maxLength={6}
              value={form.otp}
              onChange={(e) => setForm({ ...form, otp: e.target.value })}
              className="msm-input"
              placeholder="000000"
              required
            />
          </div>
        )}
        <div className="md:col-span-2 flex justify-end">
          <Button
            type="submit"
            disabled={submitting}
            className="inline-flex items-center gap-2 disabled:opacity-50"
          >
            {submitting ? (
              <Spinner />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {t('common.save')}
          </Button>
        </div>
      </form>
    </div>
  )
}
