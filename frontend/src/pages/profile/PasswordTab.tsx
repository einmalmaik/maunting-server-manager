import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { api } from '@/api/client'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { KeyRound, Save } from 'lucide-react'

/**
 * Tab: Passwort aendern.
 * Validiert lokal (Laenge, Match), ruft /auth/change-password,
 * beruecksichtigt 2FA-OTP, falls der User 2FA aktiviert hat.
 */
export function PasswordTab() {
  const { t } = useTranslation()
  const { user } = useAuthStore()
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
          otp_code: user?.two_factor_enabled ? form.otp : null,
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

  return (
    <div className="msm-card p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
          <KeyRound className="w-5 h-5 text-secondary" />
        </div>
        <h2 className="font-headline text-headline-sm text-primary">{t('profile.changePassword')}</h2>
      </div>

      {error && <div className="msm-alert-error text-sm mb-4">{error}</div>}
      {success && <div className="msm-alert-success text-sm mb-4">{success}</div>}

      <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="md:col-span-2">
          <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('profile.currentPassword')}
          </label>
          <PasswordInput
            value={form.current}
            onChange={(e) => setForm({ ...form, current: e.target.value })}
            required
          />
        </div>
        <div>
          <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('profile.newPassword')}
          </label>
          <PasswordInput
            value={form.new}
            onChange={(e) => setForm({ ...form, new: e.target.value })}
            required
            minLength={8}
          />
        </div>
        <div>
          <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('profile.confirmPassword')}
          </label>
          <PasswordInput
            value={form.confirm}
            onChange={(e) => setForm({ ...form, confirm: e.target.value })}
            required
            minLength={8}
          />
        </div>
        {user?.two_factor_enabled && (
          <div className="md:col-span-2">
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('auth.otpCode')}
            </label>
            <input
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
          <button
            type="submit"
            disabled={submitting}
            className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
          >
            {submitting ? (
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {t('common.save')}
          </button>
        </div>
      </form>
    </div>
  )
}
