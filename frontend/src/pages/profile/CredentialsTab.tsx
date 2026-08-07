/**
 * Eigener Zugangsdaten-Tresor (Phase 7).
 *
 * Hier hinterlegt ein Benutzer sein Steam-Konto oder GitHub-Token. Der Wert
 * verlaesst das Backend nach dem Speichern nie wieder — sichtbar bleibt nur ein
 * Hinweis auf die letzten Zeichen. Zugewiesen wird ein Credential erst im
 * jeweiligen Server unter "Zugangsdaten".
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Plus, Save, Trash2 } from 'lucide-react'

import {
  credentialsApi,
  type CredentialKind,
  type UserCredential,
} from '@/api/credentials'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

const KINDS: CredentialKind[] = ['steam_account', 'github_token']

export function CredentialsTab() {
  const { t } = useTranslation()
  const [credentials, setCredentials] = useState<UserCredential[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [kind, setKind] = useState<CredentialKind>('steam_account')
  const [label, setLabel] = useState('')
  const [username, setUsername] = useState('')
  const [secret, setSecret] = useState('')

  const load = useCallback(async () => {
    try {
      setCredentials(await credentialsApi.listMine())
    } catch (error: unknown) {
      toast.error(
        error instanceof SanitizedApiError ? error.message : t('credentials.errors.load'),
      )
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void load()
  }, [load])

  const save = async () => {
    if (busy || !label.trim() || !secret.trim()) return
    if (kind === 'steam_account' && !username.trim()) return
    setBusy(true)
    try {
      await credentialsApi.save({
        kind,
        label: label.trim(),
        username: kind === 'steam_account' ? username.trim() : null,
        secret: secret.trim(),
      })
      // Das Geheimnis wird sofort aus dem Formularzustand entfernt.
      setSecret('')
      setLabel('')
      setUsername('')
      await load()
      toast.success(t('credentials.saved'))
    } catch (error: unknown) {
      toast.error(
        error instanceof SanitizedApiError ? error.message : t('credentials.errors.save'),
      )
    } finally {
      setBusy(false)
    }
  }

  const remove = async (credential: UserCredential) => {
    if (busy) return
    const accepted = await confirm({
      title: t('credentials.deleteTitle'),
      message: t('credentials.deleteConfirm', { label: credential.label }),
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!accepted) return
    setBusy(true)
    try {
      await credentialsApi.remove(credential.id)
      await load()
      toast.success(t('credentials.deleted'))
    } catch (error: unknown) {
      toast.error(
        error instanceof SanitizedApiError ? error.message : t('credentials.errors.delete'),
      )
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="flex h-32 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  return (
    <section className="space-y-4" aria-labelledby="credentials-title">
      <div className="msm-card p-6">
        <div className="flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h3 id="credentials-title" className="font-headline text-lg font-semibold text-on-surface">
            {t('credentials.title')}
          </h3>
        </div>
        <p className="mt-2 text-sm text-on-surface-variant">{t('credentials.description')}</p>
      </div>

      {credentials.length === 0 && (
        <div className="msm-card p-6 text-sm text-on-surface-variant">
          {t('credentials.empty')}
        </div>
      )}

      {credentials.length > 0 && (
        <ul className="msm-card divide-y divide-outline-variant/40 p-0">
          {credentials.map((credential) => (
            <li key={credential.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div className="min-w-0">
                <p className="text-sm font-medium text-on-surface">{credential.label}</p>
                <p className="text-xs text-on-surface-variant">
                  {t(`credentials.kinds.${credential.kind}`)}
                  {credential.username ? ` · ${credential.username}` : ''}
                  {credential.secret_hint ? ` · ${credential.secret_hint}` : ''}
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => void remove(credential)}
                aria-label={t('common.delete')}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form
        className="msm-card space-y-4 p-6"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <div className="flex items-center gap-2">
          <Plus className="h-4 w-4 text-secondary" aria-hidden="true" />
          <h4 className="font-headline text-base font-semibold text-on-surface">
            {t('credentials.add')}
          </h4>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <label className="space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('credentials.kind')}
            </span>
            <select
              className="msm-input"
              value={kind}
              onChange={(event) => setKind(event.target.value as CredentialKind)}
            >
              {KINDS.map((value) => (
                <option key={value} value={value}>{t(`credentials.kinds.${value}`)}</option>
              ))}
            </select>
          </label>
          <label className="space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('credentials.label')}
            </span>
            <input
              className="msm-input"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              maxLength={64}
            />
          </label>
          {kind === 'steam_account' && (
            <label className="space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('credentials.username')}
              </span>
              <input
                className="msm-input"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="off"
                maxLength={256}
              />
            </label>
          )}
          <label className="space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('credentials.secret')}
            </span>
            <input
              className="msm-input"
              type="password"
              autoComplete="new-password"
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              maxLength={4096}
            />
          </label>
        </div>
        <p className="text-xs text-on-surface-variant">{t('credentials.secretHint')}</p>
        <div className="flex justify-end">
          <Button
            type="submit"
            disabled={
              busy
              || !label.trim()
              || !secret.trim()
              || (kind === 'steam_account' && !username.trim())
            }
          >
            <Save className="h-4 w-4" aria-hidden="true" />{t('credentials.save')}
          </Button>
        </div>
      </form>
    </section>
  )
}
