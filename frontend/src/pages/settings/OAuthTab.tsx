import { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  KeyRound, Plus, Pencil, Trash2, FlaskConical, Save, Copy, Check, ShieldCheck, X,
} from 'lucide-react'
import { oauthApi, OAUTH_PRESETS, type OAuthProvider, type OAuthPreset, type OAuthSwitches } from '@/api/oauth'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { confirm } from '@/stores/confirmStore'
import { Switch } from '@/components/ui/Switch'

const SECRET_MASK_PATTERN = /^[*]+$/

function isSecretMasked(value: string): boolean {
  return SECRET_MASK_PATTERN.test(value)
}

interface FormState {
  id: number | null
  slug: string
  name: string
  preset: OAuthPreset
  enabled: boolean
  client_id: string
  client_secret: string
  client_secret_present: boolean
  issuer: string
  authorization_endpoint: string
  token_endpoint: string
  userinfo_endpoint: string
  scope: string
  claims_mapping_json: string
  position: number
}

const EMPTY_FORM: FormState = {
  id: null,
  slug: '',
  name: '',
  preset: 'google',
  enabled: true,
  client_id: '',
  client_secret: '',
  client_secret_present: false,
  issuer: '',
  authorization_endpoint: '',
  token_endpoint: '',
  userinfo_endpoint: '',
  scope: '',
  claims_mapping_json: '',
  position: 0,
}

function providerToForm(p: OAuthProvider): FormState {
  return {
    id: p.id,
    slug: p.slug,
    name: p.name,
    preset: (p.preset as OAuthPreset) || 'custom_oauth2',
    enabled: p.enabled,
    client_id: p.client_id,
    client_secret: p.client_secret,
    client_secret_present: !!p.client_secret && p.client_secret.length > 0,
    issuer: p.issuer ?? '',
    authorization_endpoint: p.authorization_endpoint ?? '',
    token_endpoint: p.token_endpoint ?? '',
    userinfo_endpoint: p.userinfo_endpoint ?? '',
    scope: p.scope ?? '',
    claims_mapping_json: p.claims_mapping_json ?? '',
    position: p.position,
  }
}

export function OAuthTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')

  const [providers, setProviders] = useState<OAuthProvider[]>([])
  const [switches, setSwitches] = useState<OAuthSwitches | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<FormState | null>(null)
  const [savingForm, setSavingForm] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [savingSwitches, setSavingSwitches] = useState(false)
  const [copiedId, setCopiedId] = useState<number | null>(null)

  const load = async () => {
    try {
      const [list, sw] = await Promise.all([
        oauthApi.listProviders(),
        oauthApi.getSwitches(),
      ])
      setProviders(list)
      setSwitches(sw)
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const sortedProviders = useMemo(
    () => [...providers].sort((a, b) => a.position - b.position || a.id - b.id),
    [providers],
  )

  const openNew = () => setEditing({ ...EMPTY_FORM, position: providers.length })
  const openEdit = (p: OAuthProvider) => setEditing(providerToForm(p))
  const closeEdit = () => setEditing(null)

  const handleSaveForm = async () => {
    if (!editing) return
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(editing.slug) || editing.slug.length < 2) {
      toast.error(t('settings.oauth.providerSlugHint'))
      return
    }
    setSavingForm(true)
    try {
      const isUpdate = editing.id !== null
      const baseBody = {
        name: editing.name.trim(),
        preset: editing.preset,
        enabled: editing.enabled,
        client_id: editing.client_id,
        issuer: editing.issuer.trim() || null,
        authorization_endpoint: editing.authorization_endpoint.trim() || null,
        token_endpoint: editing.token_endpoint.trim() || null,
        userinfo_endpoint: editing.userinfo_endpoint.trim() || null,
        scope: editing.scope.trim() || null,
        claims_mapping_json: editing.claims_mapping_json.trim() || null,
        position: editing.position,
      }
      if (isUpdate) {
        const body: Record<string, unknown> = { ...baseBody }
        if (editing.client_secret && !isSecretMasked(editing.client_secret)) {
          body.client_secret = editing.client_secret
        } else if (editing.client_secret === '') {
          body.client_secret = ''
        }
        await oauthApi.updateProvider(editing.id as number, body)
        toast.success(t('settings.oauth.updated'))
      } else {
        const body: Record<string, unknown> = {
          slug: editing.slug,
          ...baseBody,
        }
        if (editing.client_secret) {
          body.client_secret = editing.client_secret
        }
        await oauthApi.createProvider(body as never)
        toast.success(t('settings.oauth.saved'))
      }
      closeEdit()
      await load()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSavingForm(false)
    }
  }

  const handleDelete = async (p: OAuthProvider) => {
    const ok = await confirm({
      message: t('settings.oauth.deleteConfirmBody', { name: p.name, slug: p.slug }),
      danger: true,
      confirmText: t('settings.oauth.delete'),
    })
    if (!ok) return
    try {
      await oauthApi.deleteProvider(p.id)
      toast.success(t('settings.oauth.deleted'))
      await load()
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  const handleTest = async (p: OAuthProvider) => {
    setTestingId(p.id)
    try {
      const res = await oauthApi.testProvider(p.id)
      if (res.ok) {
        toast.success(res.message || t('settings.oauth.testOk'))
      } else {
        toast.error(t('settings.oauth.testFailed', { message: res.message }))
      }
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setTestingId(null)
    }
  }

  const handleUpdateSwitches = async (patch: Partial<OAuthSwitches>) => {
    if (!switches) return
    setSavingSwitches(true)
    try {
      const next = { ...switches, ...patch }
      setSwitches(next)
      const updated = await oauthApi.updateSwitches(patch)
      setSwitches(updated)
      toast.success(t('settings.oauth.switchesSaved'))
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSavingSwitches(false)
    }
  }

  const copyRedirectUri = async (slug: string, id: number) => {
    const origin = window.location.origin
    const uri = `${origin}/api/oauth/${slug}/callback`
    try {
      await navigator.clipboard.writeText(uri)
      setCopiedId(id)
      window.setTimeout(() => setCopiedId(null), 1500)
    } catch {
      // Clipboard ist Komfort, kein kritischer Pfad.
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Provider list */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <KeyRound className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary flex-1">
            {t('settings.oauth.providers')}
          </h2>
          {canWrite && (
            <button
              type="button"
              onClick={openNew}
              className="msm-btn-primary px-3 py-2 text-sm inline-flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              {t('settings.oauth.addProvider')}
            </button>
          )}
        </div>

        {sortedProviders.length === 0 ? (
          <p className="font-body-md text-sm text-on-surface-variant py-8 text-center">
            {t('settings.oauth.noProviders')}
          </p>
        ) : (
          <ul className="divide-y divide-outline-variant/30">
            {sortedProviders.map((p) => {
              const callbackUri = `${window.location.origin}/api/oauth/${p.slug}/callback`
              const isCopied = copiedId === p.id
              return (
                <li key={p.id} className="py-4 first:pt-0 last:pb-0 flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-label-md text-sm font-medium text-on-surface">{p.name}</span>
                      <span className="font-mono-sm text-xs text-on-surface-variant">({p.slug})</span>
                      <span className="text-xs px-1.5 py-0.5 rounded bg-surface-container-high text-on-surface-variant">
                        {t(`settings.oauth.preset.${p.preset}` as any, p.preset)}
                      </span>
                      {!p.enabled && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-status-error/10 text-status-error border border-status-error/30">
                          {t('settings.oauth.providerEnabled')}: off
                        </span>
                      )}
                    </div>
                    <div className="mt-2 flex items-center gap-2 text-xs">
                      <code className="font-mono-sm text-on-surface-variant bg-surface-container-low px-2 py-0.5 rounded truncate max-w-md">
                        {callbackUri}
                      </code>
                      <button
                        type="button"
                        onClick={() => copyRedirectUri(p.slug, p.id)}
                        className="text-on-surface-variant hover:text-on-surface inline-flex items-center gap-1"
                        title={t('common.copy')}
                      >
                        {isCopied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>
                  {canWrite && (
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={() => handleTest(p)}
                        disabled={testingId === p.id}
                        className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {testingId === p.id ? (
                          <span className="w-3.5 h-3.5 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                        ) : (
                          <FlaskConical className="w-3.5 h-3.5" />
                        )}
                        {t('settings.oauth.test')}
                      </button>
                      <button
                        type="button"
                        onClick={() => openEdit(p)}
                        className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5"
                        title={t('settings.oauth.edit')}
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(p)}
                        className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5 text-status-error hover:bg-status-error/10"
                        title={t('settings.oauth.delete')}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* Global switches */}
      {switches && (
        <div className="msm-card p-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
              <ShieldCheck className="w-5 h-5 text-secondary" />
            </div>
            <h2 className="font-headline text-headline-sm text-primary">
              {t('settings.oauth.switches')}
            </h2>
          </div>

          <div className="space-y-4">
            <SwitchRow
              label={t('settings.oauth.switchAllowRegistration')}
              hint={t('settings.oauth.switchAllowRegistrationHint')}
              checked={switches.allow_registration}
              disabled={!canWrite || savingSwitches}
              onChange={(v) => handleUpdateSwitches({ allow_registration: v })}
            />
            <SwitchRow
              label={t('settings.oauth.switchAllowLinking')}
              hint={t('settings.oauth.switchAllowLinkingHint')}
              checked={switches.allow_linking}
              disabled={!canWrite || savingSwitches}
              onChange={(v) => handleUpdateSwitches({ allow_linking: v })}
            />
            <SwitchRow
              label={t('settings.oauth.switchRequireVerifiedEmail')}
              hint={t('settings.oauth.switchRequireVerifiedEmailHint')}
              checked={switches.require_verified_email}
              disabled={!canWrite || savingSwitches}
              onChange={(v) => handleUpdateSwitches({ require_verified_email: v })}
            />
          </div>
        </div>
      )}

      {editing && (
        <ProviderDialog
          form={editing}
          setForm={setEditing}
          onClose={closeEdit}
          onSave={handleSaveForm}
          saving={savingForm}
        />
      )}
    </div>
  )
}

function SwitchRow({
  label, hint, checked, disabled, onChange,
}: {
  label: string
  hint: string
  checked: boolean
  disabled: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2">
      <div className="flex-1 min-w-0">
        <p className="font-label-md text-sm text-on-surface font-medium">{label}</p>
        <p className="font-body-md text-xs text-on-surface-variant mt-0.5">{hint}</p>
      </div>
      <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </div>
  )
}

function ProviderDialog({
  form, setForm, onClose, onSave, saving,
}: {
  form: FormState
  setForm: (f: FormState) => void
  onClose: () => void
  onSave: () => void
  saving: boolean
}) {
  const { t } = useTranslation()
  const isCustom = form.preset === 'custom_oidc' || form.preset === 'custom_oauth2'
  const showIssuer = form.preset === 'custom_oidc'
  const showEndpointOverrides = isCustom
  const showClaims = isCustom
  const showScope = isCustom || form.preset === 'twitter'

  const callbackUri = `${window.location.origin}/api/oauth/${form.slug || '<slug>'}/callback`

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="msm-card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-6">
          <h2 className="font-headline text-headline-sm text-primary">
            {form.id === null ? t('settings.oauth.createTitle') : t('settings.oauth.editTitle')}
          </h2>
          <button type="button" onClick={onClose} className="text-on-surface-variant hover:text-on-surface">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.oauth.providerSlug')}
              </label>
              <input
                type="text"
                value={form.slug}
                onChange={(e) => setForm({ ...form, slug: e.target.value })}
                className="msm-input font-mono text-sm"
                placeholder="my-google"
                readOnly={form.id !== null}
                disabled={form.id !== null}
              />
              <p className="font-body-md text-xs text-on-surface-variant mt-1">
                {t('settings.oauth.providerSlugHint')}
              </p>
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.oauth.providerName')}
              </label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="msm-input"
                placeholder="Google"
              />
            </div>
          </div>

          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.oauth.providerPreset')}
            </label>
            <select
              value={form.preset}
              onChange={(e) => setForm({ ...form, preset: e.target.value as OAuthPreset })}
              className="msm-input"
              disabled={form.id !== null}
            >
              {OAUTH_PRESETS.map((p) => (
                <option key={p} value={p}>
                  {t(`settings.oauth.preset.${p}` as any, p)}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center justify-between py-2 px-3 rounded-md bg-surface-container-low">
            <span className="font-label-md text-sm text-on-surface">{t('settings.oauth.providerEnabled')}</span>
            <Switch
              checked={form.enabled}
              onCheckedChange={(v) => setForm({ ...form, enabled: v })}
            />
          </div>

          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.oauth.providerClientId')}
            </label>
            <input
              type="text"
              value={form.client_id}
              onChange={(e) => setForm({ ...form, client_id: e.target.value })}
              className="msm-input font-mono text-sm"
              placeholder="…apps.googleusercontent.com"
            />
          </div>

          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.oauth.providerClientSecret')}
            </label>
            <input
              type="text"
              value={form.client_secret}
              onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
              className="msm-input font-mono text-sm"
              placeholder={form.client_secret_present ? '•••••••• (leave empty to keep)' : 'GOCSPX-…'}
              autoComplete="off"
            />
            <p className="font-body-md text-xs text-on-surface-variant mt-1">
              {t('settings.oauth.providerSecretHint')}
            </p>
          </div>

          {showIssuer && (
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.oauth.providerIssuer')}
              </label>
              <input
                type="text"
                value={form.issuer}
                onChange={(e) => setForm({ ...form, issuer: e.target.value })}
                className="msm-input font-mono text-sm"
                placeholder="https://auth.example.com"
              />
            </div>
          )}

          {showEndpointOverrides && (
            <>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.oauth.providerAuthz')}
                </label>
                <input
                  type="text"
                  value={form.authorization_endpoint}
                  onChange={(e) => setForm({ ...form, authorization_endpoint: e.target.value })}
                  className="msm-input font-mono text-sm"
                  placeholder="https://auth.example.com/oauth2/authorize"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.oauth.providerToken')}
                </label>
                <input
                  type="text"
                  value={form.token_endpoint}
                  onChange={(e) => setForm({ ...form, token_endpoint: e.target.value })}
                  className="msm-input font-mono text-sm"
                  placeholder="https://auth.example.com/oauth2/token"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.oauth.providerUserinfo')}
                </label>
                <input
                  type="text"
                  value={form.userinfo_endpoint}
                  onChange={(e) => setForm({ ...form, userinfo_endpoint: e.target.value })}
                  className="msm-input font-mono text-sm"
                  placeholder="https://auth.example.com/oauth2/userinfo"
                />
              </div>
            </>
          )}

          {showScope && (
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.oauth.providerScope')}
              </label>
              <input
                type="text"
                value={form.scope}
                onChange={(e) => setForm({ ...form, scope: e.target.value })}
                className="msm-input font-mono text-sm"
                placeholder="openid email profile"
              />
            </div>
          )}

          {showClaims && (
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.oauth.providerClaims')}
              </label>
              <textarea
                value={form.claims_mapping_json}
                onChange={(e) => setForm({ ...form, claims_mapping_json: e.target.value })}
                className="msm-input font-mono text-sm min-h-[80px]"
                placeholder='{"id":"sub","email":"email"}'
              />
              <p className="font-body-md text-xs text-on-surface-variant mt-1">
                {t('settings.oauth.providerClaimsHint')}
              </p>
            </div>
          )}

          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.oauth.providerPosition')}
            </label>
            <input
              type="number"
              value={form.position}
              onChange={(e) => setForm({ ...form, position: Number(e.target.value) })}
              className="msm-input w-24"
            />
          </div>

          {form.slug && (
            <div className="p-3 bg-surface-container-low rounded-md border border-outline-variant/30">
              <p className="font-label-md text-xs text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.oauth.redirectUri')}
              </p>
              <code className="font-mono-sm text-xs text-on-surface break-all">{callbackUri}</code>
              <p className="font-body-md text-xs text-on-surface-variant mt-1.5">
                {t('settings.oauth.redirectUriHint')}
              </p>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 mt-6 pt-6 border-t border-outline-variant/30">
          <button type="button" onClick={onClose} className="msm-btn-secondary px-4 py-2">
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
          >
            {saving ? (
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {t('settings.oauth.save')}
          </button>
        </div>
      </div>
    </div>
  )
}
