import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { api } from '@/api/client'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { useZweitfaktor, ZweitfaktorFeld } from '@/components/auth/ZweitfaktorNachweis'

import { Spinner } from '@/components/ui/Spinner'
/**
 * Tab: Gefahrenzone - Konto loeschen.
 *
 * Eigener Tab mit Danger-Variante, damit der Loesch-Workflow nicht versehentlich
 * zwischen den normalen Tabs uebersehen wird. Fuer Konten ohne eigenes Passwort
 * (nur Social Login) entfaellt die Passwort-Bestaetigung; das Backend ist dabei
 * die einzige Wahrheitsquelle.
 */
export function DangerZoneTab() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const isSocialOnly = user?.has_password === false
  const faktor = useZweitfaktor()

  const [deleteState, setDeleteState] = useState<'idle' | 'first-confirmed' | 'deleting' | 'success'>('idle')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [confirmDeleteWord, setConfirmDeleteWord] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  const handleDelete = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrorMsg('')
    setDeleteState('deleting')
    try {
      await api('/auth/delete-account', {
        method: 'DELETE',
        body: JSON.stringify({
          // Social-only Accounts ueberspringen die Passwort-Pruefung im Backend.
          // Pydantic lehnt leeren String ab, daher null statt ''.
          password: isSocialOnly ? null : confirmPassword,
          confirmation: confirmDeleteWord,
          ...(await faktor.nachweis('account_delete')),
        }),
      })
      setDeleteState('success')
      await logout()
      navigate('/login', { replace: true })
    } catch (err: any) {
      setErrorMsg(err.message)
      setDeleteState('first-confirmed')
    }
  }

  return (
    <div className="msm-card p-6 border border-status-destructive/35">
      <div className="flex items-center gap-2 mb-6">
        <AlertTriangle className="h-5 w-5 text-status-destructive" aria-hidden="true" />
        <div className="flex-1">
          {/* Die Warnfarbe bleibt: sie unterscheidet die Gefahrenzone von den
              übrigen Karten. Nur Größe und Bauweise ziehen mit. */}
          <h2 className="font-headline text-title-lg font-semibold text-status-destructive">{t('profile.deleteAccountTitle')}</h2>
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
            <Button variant="destructive"
              onClick={() => setDeleteState('first-confirmed')}
            >
              {t('profile.deleteAccountBtn')}
            </Button>
          )}

          {deleteState !== 'idle' && deleteState !== 'success' && (
            <form
              onSubmit={handleDelete}
              className="space-y-4 border-t border-outline-variant/30 pt-4"
            >
              <div className="p-4 bg-status-destructive/5 border border-status-destructive/20 rounded-lg">
                <p className="font-label-md text-sm text-status-destructive font-medium mb-1">
                  {t('profile.deleteAccountWarningTitle')}
                </p>
                <p className="font-body-md text-xs text-on-surface-variant">
                  {t('profile.deleteAccountWarningText')}
                </p>
              </div>

              {!isSocialOnly && (
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('profile.confirmPasswordLabel')}
                  </label>
                  <PasswordInput
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required={!isSocialOnly}
                    disabled={deleteState === 'deleting'}
                  />
                </div>
              )}

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
                    // Paste ist absichtlich blockiert.
                  }}
                  className="msm-input font-mono"
                  placeholder="delete"
                  required
                  disabled={deleteState === 'deleting'}
                  autoComplete="off"
                  spellCheck={false}
                />
                <p className="text-label-sm text-on-surface-variant mt-1">{t('profile.confirmDeleteWordHint')}</p>
              </div>

              <ZweitfaktorFeld
                faktor={faktor}
                id="delete-account-otp"
                label={t('profile.confirmOtpLabel')}
                disabled={deleteState === 'deleting'}
              />

              {errorMsg && <div className="msm-alert-error text-sm">{errorMsg}</div>}

              <div className="flex flex-wrap gap-3">
                <Button variant="destructive"
                  type="submit"
                  disabled={deleteState === 'deleting'}
                  className="inline-flex items-center gap-2"
                >
                  {deleteState === 'deleting' ? (
                    <Spinner />
                  ) : (
                    t('profile.deleteAccountFinalBtn')
                  )}
                </Button>
                <Button variant="secondary"
                  type="button"
                  onClick={() => {
                    setDeleteState('idle')
                    setConfirmPassword('')
                    setConfirmDeleteWord('')
                    faktor.setCode('')
                    setErrorMsg('')
                  }}
                  disabled={deleteState === 'deleting'}
                >
                  {t('common.cancel')}
                </Button>
              </div>
            </form>
          )}
        </>
      )}
    </div>
  )
}
