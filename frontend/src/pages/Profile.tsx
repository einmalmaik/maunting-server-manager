import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { api } from '@/api/client'
import { oauthApi, type OAuthProviderPublic, type OAuthUserLink } from '@/api/oauth'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { Shield, Mail, KeyRound, Check, Save, AlertTriangle, Download, RotateCcw, Link2, Unlink } from 'lucide-react'

export function Profile() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { user, setUser, logout } = useAuthStore()
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const [deleteState, setDeleteState] = useState<'idle' | 'first-confirmed' | 'deleting' | 'success'>('idle')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [confirmDeleteWord, setConfirmDeleteWord] = useState('')
  const [confirmOtp, setConfirmOtp] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  const [pwdForm, setPwdForm] = useState({
    current: '',
    new: '',
    confirm: '',
    otp: '',
  })

  const [show2FASetup, setShow2FASetup] = useState(false)
  const [show2FADisable, setShow2FADisable] = useState(false)
  const [otpCode, setOtpCode] = useState('')
  const [faSecret, setFaSecret] = useState('')
  const [faUri, setFaUri] = useState('')
  const [backupCodes, setBackupCodes] = useState<string[]>([])

  const [oauthLinks, setOauthLinks] = useState<OAuthUserLink[]>([])
// For deletion: social-only (has links) can skip current-password step.
// Central truth is in backend (has OAuthUserLink), here we use the loaded links for UI.
const isSocialOnlyForDeletion = oauthLinks.length > 0
  const [oauthAvailable, setOauthAvailable] = useState<OAuthProviderPublic[]>([])
  const [oauthLoading, setOauthLoading] = useState(true)
  const [unlinkingId, setUnlinkingId] = useState<number | null>(null)

  const loadOauth = async () => {
    try {
      const [links, publicProviders] = await Promise.all([
        oauthApi.listMyLinks(),
        oauthApi.listPublicProviders(),
      ])
      setOauthLinks(links)
      setOauthAvailable(publicProviders)
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setOauthLoading(false)
    }
  }

  useEffect(() => { loadOauth() }, [])

  // URL-Param-Auswertung fuer OAuth-Linking-Callback (redirect nach /profile?linked=1|error=…)
  useEffect(() => {
    const linked = searchParams.get('linked')
    const linkError = searchParams.get('error')
    if (linked === '1') {
      toast.success(t('profile.linkedAccounts.linkSuccess'))
      setSearchParams({}, { replace: true })
      void loadOauth()
    } else if (linkError) {
      const key = `profile.linkedAccounts.linkError${linkError
        .split('_')
        .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
        .join('')}`
      const translated = t(key, '')
      toast.error(translated || t('profile.linkedAccounts.linkErrorUnknown'))
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams, t])

  const handleUnlink = async (link: OAuthUserLink) => {
    const ok = await confirm({
      message: t('profile.linkedAccounts.unlinkConfirm', { provider: link.provider_name }),
      danger: true,
      confirmText: t('profile.linkedAccounts.unlink'),
    })
    if (!ok) return
    setUnlinkingId(link.id)
    try {
      await oauthApi.unlinkProvider(link.provider_id)
      toast.success(t('profile.linkedAccounts.unlinkSuccess'))
      await loadOauth()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setUnlinkingId(null)
    }
  }

  const formatDate = (iso: string | null): string => {
    if (!iso) return t('profile.linkedAccounts.neverUsed')
    try {
      return new Intl.DateTimeFormat(i18n.language, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(new Date(iso))
    } catch {
      return iso
    }
  }

  const linkedSlugs = new Set(oauthLinks.map((l) => l.provider_slug))
  const unlinkedProviders = oauthAvailable.filter((p) => !linkedSlugs.has(p.slug))

  const buildBackupCodeFile = (codes: string[]) => {
    const generatedAt = new Intl.DateTimeFormat(i18n.language, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date())

    return [
      t('profile.backupCodesFileTitle'),
      '',
      t('profile.backupCodesFileIntro'),
      t('profile.backupCodesWarning'),
      '',
      `${t('profile.backupCodesFileUser')}: ${user?.email || user?.username || '-'}`,
      `${t('profile.backupCodesFileGeneratedAt')}: ${generatedAt}`,
      '',
      ...codes,
      '',
      t('profile.backupCodesDownloadOnce'),
    ].join('\n')
  }

  const downloadBackupCodes = () => {
    if (backupCodes.length === 0) return
    const blob = new Blob([buildBackupCodeFile(backupCodes)], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    const date = new Date().toISOString().slice(0, 10)
    link.href = url
    link.download = `msm-backup-codes-${date}.txt`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    setBackupCodes([])
    setSuccess(t('profile.backupCodesDownloaded'))
    setTimeout(() => setSuccess(''), 5000)
  }

  const regenerateBackupCodes = async () => {
    const ok = await confirm({
      message: t('profile.regenerateBackupCodesConfirm'),
      danger: true,
      confirmText: t('profile.regenerateBackupCodes'),
    })
    if (!ok) return

    setError('')
    setSubmitting(true)
    try {
      const codesRes = await api<{ codes: string[] }>('/auth/2fa/backup/generate', { method: 'POST' })
      setBackupCodes(codesRes.codes)
      setSuccess(t('profile.backupCodesRegenerated'))
      setTimeout(() => setSuccess(''), 5000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSuccess('')

    if (pwdForm.new !== pwdForm.confirm) {
      setError(t('profile.passwordMismatch'))
      return
    }
    if (pwdForm.new.length < 8) {
      setError(t('auth.passwordTooShort'))
      return
    }

    setSubmitting(true)
    try {
      await api('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: pwdForm.current,
          new_password: pwdForm.new,
          otp_code: user?.two_factor_enabled ? pwdForm.otp : null,
        }),
      })
      setSuccess(t('profile.passwordChanged'))
      setPwdForm({ current: '', new: '', confirm: '', otp: '' })
      setTimeout(() => setSuccess(''), 3000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleSetup2FA = async () => {
    setError('')
    try {
      const res = await api<{ secret: string; uri: string }>('/auth/2fa/setup', {
        method: 'POST',
      })
      setFaSecret(res.secret)
      setFaUri(res.uri)
      setShow2FASetup(true)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const handleEnable2FA = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await api('/auth/2fa/enable?otp_code=' + otpCode, { method: 'POST' })
      // Backup-Codes generieren
      const codesRes = await api<{ codes: string[] }>('/auth/2fa/backup/generate', { method: 'POST' })
      setBackupCodes(codesRes.codes)
      // User-Data aktualisieren
      const updated = await api<{ two_factor_enabled: boolean }>('/auth/me')
      if (user && updated) {
        setUser({ ...user, two_factor_enabled: true })
      }
      setShow2FASetup(false)
      setOtpCode('')
      setSuccess(t('profile.2faEnabled'))
      setTimeout(() => setSuccess(''), 5000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleDisable2FA = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await api('/auth/2fa/disable?otp_code=' + otpCode, { method: 'POST' })
      if (user) {
        setUser({ ...user, two_factor_enabled: false })
      }
      setShow2FADisable(false)
      setOtpCode('')
      setSuccess(t('profile.2faDisabled'))
      setTimeout(() => setSuccess(''), 3000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('profile.title')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          {t('profile.subtitle')}
        </p>
      </div>

      {error && <div className="msm-alert-error text-sm">{error}</div>}
      {success && <div className="msm-alert-success text-sm">{success}</div>}

      {/* Account Info */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <Mail className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('auth.email')}</h2>
        </div>
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-lg font-bold text-primary border border-outline-variant">
            {user?.username.charAt(0).toUpperCase()}
          </div>
          <div>
            <p className="font-label-md text-sm text-on-surface font-medium">{user?.username}</p>
            <p className="font-body-md text-sm text-on-surface-variant">{user?.email}</p>
            {user?.email_verified === false && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-status-error/10 text-status-error border border-status-error/30 mt-1">
                <AlertTriangle className="w-3 h-3" />
                {t('profile.notVerified', 'Nicht verifiziert')}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Change Password */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <KeyRound className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('profile.changePassword')}</h2>
        </div>

        <form onSubmit={handleChangePassword} className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2">
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('profile.currentPassword')}
            </label>
            <PasswordInput
              value={pwdForm.current}
              onChange={(e) => setPwdForm({ ...pwdForm, current: e.target.value })}
              required
            />
          </div>
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('profile.newPassword')}
            </label>
            <PasswordInput
              value={pwdForm.new}
              onChange={(e) => setPwdForm({ ...pwdForm, new: e.target.value })}
              required
              minLength={8}
            />
          </div>
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('profile.confirmPassword')}
            </label>
            <PasswordInput
              value={pwdForm.confirm}
              onChange={(e) => setPwdForm({ ...pwdForm, confirm: e.target.value })}
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
                value={pwdForm.otp}
                onChange={(e) => setPwdForm({ ...pwdForm, otp: e.target.value })}
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

      {/* 2FA */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <Shield className="w-5 h-5 text-secondary" />
          </div>
          <div className="flex-1">
            <h2 className="font-headline text-headline-sm text-primary">{t('profile.2faStatus')}</h2>
            <p className="font-body-md text-sm text-on-surface-variant mt-1">
              {user?.two_factor_enabled ? t('profile.2faEnabled') : t('profile.2faDisabled')}
            </p>
          </div>
          {user?.two_factor_enabled ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-status-success/10 text-status-success border border-status-success/30">
              <Check className="w-3 h-3" />
              {t('profile.2faEnabled')}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-status-error/10 text-status-error border border-status-error/30">
              <AlertTriangle className="w-3 h-3" />
              {t('profile.2faDisabled')}
            </span>
          )}
        </div>

        {!user?.two_factor_enabled && !show2FASetup && (
          <button
            onClick={handleSetup2FA}
            className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2"
          >
            <Shield className="w-4 h-4" />
            {t('profile.2faSetup')}
          </button>
        )}

        {user?.two_factor_enabled && !show2FADisable && (
          <div className="flex flex-wrap gap-3">
            <button
              onClick={regenerateBackupCodes}
              disabled={submitting}
              className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              <RotateCcw className="w-4 h-4" />
              {t('profile.regenerateBackupCodes')}
            </button>
            <button
              onClick={() => setShow2FADisable(true)}
              className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2"
            >
              <Shield className="w-4 h-4" />
              {t('profile.2faDisable')}
            </button>
          </div>
        )}

        {/* 2FA Setup Flow */}
        {show2FASetup && (
          <div className="mt-4 space-y-4 border-t border-outline-variant/30 pt-4">
            <p className="font-body-md text-sm text-on-surface-variant">{t('profile.2faScan')}</p>
            {faUri && (
              <div className="flex flex-col items-center gap-4">
                <img
                  src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(faUri)}`}
                  alt={t('profile.2faQrCode', '2FA QR Code')}
                  className="rounded-lg border border-outline-variant"
                />
                <p className="font-mono-sm text-mono-sm text-on-surface-variant bg-surface-container-high px-3 py-1.5 rounded border border-outline-variant select-all">
                  {faSecret}
                </p>
              </div>
            )}
            <form onSubmit={handleEnable2FA} className="mx-auto flex max-w-xs flex-col gap-3">
              <label className="font-body-md text-sm text-on-surface-variant text-center">
                {t('profile.2faEnterCode')}
              </label>
              <input
                type="text"
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                value={otpCode}
                onChange={(e) => setOtpCode(e.target.value)}
                className="msm-input text-center text-xl tracking-[0.5em] font-mono"
                placeholder="000000"
                required
              />
              <button
                type="submit"
                disabled={submitting || otpCode.length !== 6}
                className="msm-btn-primary px-4 py-2 disabled:opacity-50 whitespace-nowrap"
              >
                {submitting ? (
                  <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                ) : (
                  t('common.save')
                )}
              </button>
            </form>
          </div>
        )}

        {/* 2FA Disable Flow */}
        {show2FADisable && (
          <div className="mt-4 space-y-4 border-t border-outline-variant/30 pt-4">
            <p className="font-body-md text-sm text-on-surface-variant">{t('profile.2faEnterCode')}</p>
            <form onSubmit={handleDisable2FA} className="flex gap-3 max-w-xs">
              <input
                type="text"
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                value={otpCode}
                onChange={(e) => setOtpCode(e.target.value)}
                className="msm-input text-center text-xl tracking-[0.5em] font-mono"
                placeholder="000000"
                required
              />
              <button
                type="submit"
                disabled={submitting || otpCode.length !== 6}
                className="msm-btn-primary px-4 py-2 disabled:opacity-50 whitespace-nowrap"
              >
                {submitting ? (
                  <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                ) : (
                  t('common.save')
                )}
              </button>
            </form>
            <button
              type="button"
              onClick={() => { setShow2FADisable(false); setOtpCode('') }}
              className="text-sm text-on-surface-variant hover:text-on-surface transition-colors"
            >
              {t('common.cancel')}
            </button>
          </div>
        )}

        {/* Backup Codes */}
        {backupCodes.length > 0 && (
          <div className="mt-4 p-4 bg-status-warning/5 border border-status-warning/20 rounded-lg">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle className="w-4 h-4 text-status-warning" />
              <p className="font-label-md text-sm text-status-warning font-medium">{t('profile.backupCodes')}</p>
            </div>
            <p className="font-body-md text-xs text-on-surface-variant mb-3">{t('profile.backupCodesWarning')}</p>
            <p className="font-body-md text-xs text-on-surface-variant mb-3">
              {t('profile.backupCodesDownloadOnce')}
            </p>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={downloadBackupCodes}
                className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2"
              >
                <Download className="w-4 h-4" />
                {t('profile.downloadBackupCodes')}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Linked OAuth Accounts */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <Link2 className="w-5 h-5 text-secondary" />
          </div>
          <div>
            <h2 className="font-headline text-headline-sm text-primary">{t('profile.linkedAccounts.title')}</h2>
            <p className="font-body-md text-sm text-on-surface-variant mt-1">
              {t('profile.linkedAccounts.subtitle')}
            </p>
          </div>
        </div>

        {oauthLoading ? (
          <div className="flex items-center justify-center h-24">
            <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <div className="space-y-4">
            {oauthLinks.length === 0 ? (
              <p className="font-body-md text-sm text-on-surface-variant">
                {t('profile.linkedAccounts.empty')}
              </p>
            ) : (
              <ul className="divide-y divide-outline-variant/30">
                {oauthLinks.map((link) => (
                  <li key={link.id} className="py-3 first:pt-0 last:pb-0 flex items-center gap-4">
                    <div className="flex-1 min-w-0">
                      <p className="font-label-md text-sm text-on-surface font-medium">{link.provider_name}</p>
                      <p className="font-body-md text-xs text-on-surface-variant mt-0.5">
                        {t('profile.linkedAccounts.linkedSince', { date: formatDate(link.created_at) })}
                        {link.last_used_at && (
                          <span className="ml-2">
                            · {t('profile.linkedAccounts.lastUsed', { date: formatDate(link.last_used_at) })}
                          </span>
                        )}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleUnlink(link)}
                      disabled={unlinkingId === link.id}
                      className="msm-btn-secondary px-3 py-1.5 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
                    >
                      {unlinkingId === link.id ? (
                        <span className="w-3.5 h-3.5 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                      ) : (
                        <Unlink className="w-3.5 h-3.5" />
                      )}
                      {t('profile.linkedAccounts.unlink')}
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {unlinkedProviders.length > 0 && (
              <div className="pt-4 border-t border-outline-variant/30">
                <p className="font-label-md text-xs text-on-surface-variant uppercase tracking-wider mb-3">
                  {t('profile.linkedAccounts.connect')}
                </p>
                <div className="flex flex-wrap gap-2">
                  {unlinkedProviders.map((p) => (
                    <a
                      key={p.slug}
                      href={`/api/oauth/${p.slug}/link/start`}
                      className="msm-btn-secondary px-3 py-2 text-sm inline-flex items-center gap-2"
                    >
                      <Link2 className="w-3.5 h-3.5" />
                      {p.name}
                    </a>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Account loeschen */}
      <div className="msm-card p-6 border border-status-error/35">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-status-error/10 flex items-center justify-center">
            <AlertTriangle className="w-5 h-5 text-status-error" />
          </div>
          <div className="flex-1">
            <h2 className="font-headline text-headline-sm text-status-error">{t('profile.deleteAccountTitle')}</h2>
            <p className="font-body-md text-sm text-on-surface-variant mt-1">
              {t('profile.deleteAccountSubtitle')}
            </p>
          </div>
        </div>

        {user?.is_owner ? (
          <div className="msm-alert-warning text-sm mb-4">
            {t('profile.ownerCannotDelete')}
          </div>
        ) : (
          <>
            {deleteState === 'idle' && (
              <button
                onClick={() => setDeleteState('first-confirmed')}
                className="msm-btn-danger px-4 py-2"
              >
                {t('profile.deleteAccountBtn')}
              </button>
            )}

            {deleteState !== 'idle' && deleteState !== 'success' && (
              <form
                onSubmit={async (e) => {
                  e.preventDefault()
                  setErrorMsg('')
                  setDeleteState('deleting')
                  try {
                    await api('/auth/delete-account', {
                      method: 'DELETE',
                      body: JSON.stringify({
                        // For social-only accounts (has OAuth links) we send null so the backend
                        // skips the password check. Local accounts send the real password.
                        // This prevents Pydantic "String should have at least 1 character" on empty string.
                        password: isSocialOnlyForDeletion ? null : confirmPassword,
                        confirmation: confirmDeleteWord,
                        otp_code: user?.two_factor_enabled ? confirmOtp : null,
                      }),
                    })
                    setDeleteState('success')
                    await logout()
                    navigate('/login', { replace: true })
                  } catch (err: any) {
                    setErrorMsg(err.message)
                    setDeleteState('first-confirmed')
                  }
                }}
                className="space-y-4 border-t border-outline-variant/30 pt-4"
              >
                <div className="p-4 bg-status-error/5 border border-status-error/20 rounded-lg">
                  <p className="font-label-md text-sm text-status-error font-medium mb-1">
                    {t('profile.deleteAccountWarningTitle')}
                  </p>
                  <p className="font-body-md text-xs text-on-surface-variant">
                    {t('profile.deleteAccountWarningText')}
                  </p>
                </div>

                {!isSocialOnlyForDeletion && (
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('profile.confirmPasswordLabel')}
                  </label>
                  <PasswordInput
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required={!isSocialOnlyForDeletion}
                    disabled={deleteState === 'deleting'}
                  />
                </div>
                )}

                {/* Always required confirmation word "delete" - paste disabled for security/accidental prevention */}
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('profile.confirmDeleteWordLabel', { defaultValue: "Tippe 'delete' zur Bestätigung (nicht kopierbar)" })}
                  </label>
                  <input
                    type="text"
                    value={confirmDeleteWord}
                    onChange={(e) => setConfirmDeleteWord(e.target.value)}
                    onPaste={(e) => {
                      e.preventDefault();
                      // Paste is intentionally blocked. User must type the word.
                    }}
                    className="msm-input font-mono"
                    placeholder="delete"
                    required
                    disabled={deleteState === 'deleting'}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <p className="text-[10px] text-on-surface-variant mt-1">Tippe das Wort exakt ein – Kopieren/Einfügen ist deaktiviert.</p>
                </div>

                {user?.two_factor_enabled && (
                  <div>
                    <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                      {t('profile.confirmOtpLabel')}
                    </label>
                    <input
                      type="text"
                      inputMode="numeric"
                      pattern="\d{6}"
                      maxLength={6}
                      value={confirmOtp}
                      onChange={(e) => setConfirmOtp(e.target.value)}
                      className="msm-input"
                      placeholder="000000"
                      required
                      disabled={deleteState === 'deleting'}
                    />
                  </div>
                )}

                {errorMsg && <div className="msm-alert-error text-sm">{errorMsg}</div>}

                <div className="flex flex-wrap gap-3">
                  <button
                    type="submit"
                    disabled={deleteState === 'deleting'}
                    className="msm-btn-danger px-4 py-2 inline-flex items-center gap-2"
                  >
                    {deleteState === 'deleting' ? (
                      <span className="w-4 h-4 border-2 border-on-error border-t-transparent rounded-full animate-spin" />
                    ) : (
                      t('profile.deleteAccountFinalBtn')
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setDeleteState('idle')
                      setConfirmPassword('')
                      setConfirmDeleteWord('')
                      setConfirmOtp('')
                      setErrorMsg('')
                    }}
                    disabled={deleteState === 'deleting'}
                    className="msm-btn-secondary px-4 py-2"
                  >
                    {t('common.cancel')}
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </div>
    </div>
  )
}
