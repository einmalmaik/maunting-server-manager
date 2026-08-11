/**
 * Hoster-Anbindung (Phase 6).
 *
 * Die Ansicht ist reine Konfiguration. Jede Entscheidung — welcher Blueprint,
 * welche Ressourcen, welcher Kunde welchen Server sehen darf — trifft das
 * Backend. Ein frisch erzeugter API-Key oder Webhook-Secret wird genau einmal
 * angezeigt und danach verworfen; es gibt keinen Lesepfad dafuer.
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, KeyRound, Pencil, Plug, Plus, RefreshCw, Save, Trash2 } from 'lucide-react'

import {
  hosterApi,
  type HosterDelivery,
  type HosterIntegration,
  type HosterProduct,
  type HosterProductWrite,
  type HosterService,
} from '@/api/hoster'
import { credentialsApi } from '@/api/credentials'
import { SanitizedApiError } from '@/api/client'
import { Button, NumberStepper, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

const EMPTY_PRODUCT: HosterProductWrite = {
  external_product_key: '',
  game_type: '',
  ram_limit_mb: null,
  cpu_limit_percent: null,
  disk_limit_gb: null,
  node_id: null,
  backup_interval_hours: null,
  enabled: true,
}

export function HosterTab({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [integrations, setIntegrations] = useState<HosterIntegration[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(false)
  // Ein einmalig angezeigtes Geheimnis. Bewusst nur im Komponentenzustand und
  // nie in localStorage: es soll den Reload nicht ueberleben.
  const [revealed, setRevealed] = useState<{ label: string; value: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const rows = await hosterApi.listIntegrations()
      setIntegrations(rows)
      setSelectedId((current) => current ?? rows[0]?.id ?? null)
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.load'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    let active = true
    void load().then(() => { if (!active) setIntegrations([]) })
    return () => { active = false }
  }, [load])

  const selected = integrations.find((row) => row.id === selectedId) ?? null

  const rotate = async (kind: 'api-key' | 'webhook-secret') => {
    if (!selected || !canWrite || busy) return
    setBusy(true)
    try {
      const secret = kind === 'api-key'
        ? await hosterApi.rotateApiKey(selected.id)
        : await hosterApi.rotateWebhookSecret(selected.id)
      setRevealed({
        label: kind === 'api-key' ? t('hoster.apiKey') : t('hoster.webhookSecret'),
        value: secret.value,
      })
      await load()
      toast.success(t('hoster.rotated'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.rotate'))
    } finally {
      setBusy(false)
    }
  }

  const removeIntegration = async () => {
    if (!selected || !canWrite || busy) return
    const accepted = await confirm({
      title: t('hoster.deleteTitle'),
      message: t('hoster.deleteConfirm', { name: selected.name }),
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!accepted) return
    setBusy(true)
    try {
      await hosterApi.deleteIntegration(selected.id)
      setSelectedId(null)
      await load()
      toast.success(t('hoster.deleted'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.delete'))
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
    <section className="space-y-4" aria-labelledby="hoster-title">
      <div className="msm-card flex flex-wrap items-start justify-between gap-4 p-6">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2">
            <Plug className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h3 id="hoster-title" className="font-headline text-lg font-semibold text-on-surface">
              {t('hoster.title')}
            </h3>
          </div>
          <p className="mt-2 text-sm text-on-surface-variant">{t('hoster.description')}</p>
        </div>
        {canWrite && !creating && (
          <Button type="button" variant="secondary" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden="true" />{t('hoster.add')}
          </Button>
        )}
      </div>

      {revealed && (
        <SecretOnce
          label={revealed.label}
          value={revealed.value}
          onDismiss={() => setRevealed(null)}
        />
      )}

      {creating && (
        <IntegrationForm
          disabled={busy}
          onCancel={() => setCreating(false)}
          onCreated={async (secret) => {
            setCreating(false)
            setRevealed({ label: t('hoster.apiKey'), value: secret })
            await load()
          }}
        />
      )}

      {integrations.length === 0 && !creating && (
        <div className="msm-card p-6 text-sm text-on-surface-variant">{t('hoster.empty')}</div>
      )}

      {integrations.length > 0 && (
        <div className="msm-card space-y-4 p-6">
          <label className="space-y-1.5 block">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('hoster.integration')}
            </span>
            <select
              className="msm-input"
              value={selectedId ?? ''}
              onChange={(event) => setSelectedId(Number(event.target.value))}
            >
              {integrations.map((row) => (
                <option key={row.id} value={row.id}>{row.name} ({row.slug})</option>
              ))}
            </select>
          </label>

          {selected && (
            <>
              <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                <Fact label={t('hoster.apiKey')} value={selected.api_key_hint ?? '—'} />
                <Fact
                  label={t('hoster.webhookSecret')}
                  value={selected.webhook_secret_configured ? (selected.webhook_secret_hint ?? '••••') : t('hoster.notConfigured')}
                />
                <Fact label={t('hoster.webhookUrl')} value={selected.webhook_url ?? t('hoster.notConfigured')} />
                <Fact label={t('hoster.graceDays')} value={String(selected.terminate_grace_days)} />
              </dl>
              {canWrite && !editing && (
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="secondary" disabled={busy} onClick={() => setEditing(true)}>
                    <Pencil className="h-4 w-4" aria-hidden="true" />{t('common.edit')}
                  </Button>
                  <Button type="button" variant="secondary" disabled={busy} onClick={() => void rotate('api-key')}>
                    <RefreshCw className="h-4 w-4" aria-hidden="true" />{t('hoster.rotateApiKey')}
                  </Button>
                  <Button type="button" variant="secondary" disabled={busy} onClick={() => void rotate('webhook-secret')}>
                    <KeyRound className="h-4 w-4" aria-hidden="true" />{t('hoster.rotateWebhookSecret')}
                  </Button>
                  <Button type="button" variant="destructive" disabled={busy} onClick={() => void removeIntegration()}>
                    <Trash2 className="h-4 w-4" aria-hidden="true" />{t('common.delete')}
                  </Button>
                </div>
              )}
              {canWrite && editing && (
                <IntegrationEditForm
                  key={selected.id}
                  integration={selected}
                  disabled={busy}
                  onCancel={() => setEditing(false)}
                  onSaved={async () => {
                    setEditing(false)
                    await load()
                  }}
                />
              )}
            </>
          )}
        </div>
      )}

      <PanelFallbackSection canWrite={canWrite} />

      {selected && <ProductSection integrationId={selected.id} canWrite={canWrite} />}
      {selected && <ServiceSection integrationId={selected.id} />}
      {selected && <DeliverySection integrationId={selected.id} canWrite={canWrite} />}
    </section>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{label}</dt>
      <dd className="mt-0.5 break-all text-on-surface">{value}</dd>
    </div>
  )
}

/** Zeigt ein frisch erzeugtes Geheimnis genau einmal an. */
function SecretOnce({ label, value, onDismiss }: { label: string; value: string; onDismiss: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="msm-card space-y-3 border border-warning/40 p-6">
      <p className="text-sm font-semibold text-on-surface">{t('hoster.secretOnce', { label })}</p>
      <p className="text-xs text-on-surface-variant">{t('hoster.secretOnceHint')}</p>
      <code className="block break-all rounded-lg bg-surface-container-low/60 p-3 text-sm">{value}</code>
      <div className="flex flex-wrap justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            void navigator.clipboard?.writeText(value)
            toast.success(t('hoster.copied'))
          }}
        >
          <Copy className="h-4 w-4" aria-hidden="true" />{t('common.copy', 'Kopieren')}
        </Button>
        <Button type="button" onClick={onDismiss}>{t('hoster.secretUnderstood')}</Button>
      </div>
    </div>
  )
}

function IntegrationForm({
  disabled,
  onCancel,
  onCreated,
}: {
  disabled: boolean
  onCancel: () => void
  onCreated: (apiKey: string) => Promise<void>
}) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [serviceUserId, setServiceUserId] = useState('')
  const [webhookUrl, setWebhookUrl] = useState('')
  const [graceDays, setGraceDays] = useState('7')
  const [saving, setSaving] = useState(false)

  const valid = Boolean(name.trim() && slug.trim() && /^\d+$/.test(serviceUserId))

  return (
    <form
      className="msm-card space-y-5 p-6"
      onSubmit={(event) => {
        event.preventDefault()
        if (!valid || saving) return
        setSaving(true)
        hosterApi
          .createIntegration({
            name: name.trim(),
            slug: slug.trim(),
            enabled: true,
            service_user_id: Number(serviceUserId),
            webhook_url: webhookUrl.trim() || null,
            terminate_grace_days: Number(graceDays) || 0,
          })
          .then((secret) => onCreated(secret.value))
          .catch((error: unknown) => {
            toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.save'))
          })
          .finally(() => setSaving(false))
      }}
    >
      <fieldset disabled={disabled || saving} className="grid grid-cols-1 gap-4 border-0 p-0 md:grid-cols-2">
        <Field label={t('hoster.name')} value={name} onChange={setName} />
        <Field label={t('hoster.slug')} value={slug} onChange={setSlug} />
        <Field
          label={t('hoster.serviceUserId')}
          value={serviceUserId}
          onChange={setServiceUserId}
          inputMode="numeric"
        />
        <Field
          label={t('hoster.webhookUrl')}
          value={webhookUrl}
          onChange={setWebhookUrl}
          type="url"
          placeholder="https://shop.example/hooks/msm"
        />
        <div className="md:col-span-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
          <p className="text-xs text-on-surface-variant">{t('hoster.serviceUserHint')}</p>
        </div>
        <label className="space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('hoster.graceDays')}
          </span>
          <NumberStepper value={graceDays} onValueChange={setGraceDays} min={0} max={365} step={1} />
        </label>
      </fieldset>
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" disabled={saving} onClick={onCancel}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={disabled || saving || !valid}>
          <Save className="h-4 w-4" aria-hidden="true" />{saving ? t('common.loading') : t('settings.save')}
        </Button>
      </div>
    </form>
  )
}

/**
 * Nachtraegliches Aendern einer bestehenden Anbindung.
 *
 * Ohne diesen Weg war ein Tippfehler in der Webhook-Adresse endgueltig: das
 * Loeschen lehnt das Backend ab, solange noch ein Vertrag laeuft
 * (hoster_admin.delete_integration antwortet mit 409), und ein Neuanlegen
 * erzeugt zwangslaeufig einen neuen API-Key — den kennt der Shop nicht, die
 * Anbindung waere danach tot.
 *
 * Bewusst nur die drei Felder, die sich im Betrieb wirklich aendern. Name,
 * Slug und Dienstbenutzer bleiben aussen vor: der Slug steckt in den Adressen
 * des Shops, und ein Wechsel des Dienstbenutzers wuerde die Rechte aller
 * bereits erzeugten Server stillschweigend verschieben.
 */
function IntegrationEditForm({
  integration,
  disabled,
  onCancel,
  onSaved,
}: {
  integration: HosterIntegration
  disabled: boolean
  onCancel: () => void
  onSaved: () => Promise<void>
}) {
  const { t } = useTranslation()
  const [webhookUrl, setWebhookUrl] = useState(integration.webhook_url ?? '')
  const [graceDays, setGraceDays] = useState(String(integration.terminate_grace_days))
  const [enabled, setEnabled] = useState(integration.enabled)
  const [saving, setSaving] = useState(false)

  return (
    <form
      className="space-y-5 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (saving) return
        setSaving(true)
        hosterApi
          .updateIntegration(integration.id, {
            webhook_url: webhookUrl.trim() || null,
            terminate_grace_days: Number(graceDays) || 0,
            enabled,
          })
          .then(async () => {
            toast.success(t('hoster.updated'))
            await onSaved()
          })
          .catch((error: unknown) => {
            toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.save'))
          })
          .finally(() => setSaving(false))
      }}
    >
      <fieldset disabled={disabled || saving} className="grid grid-cols-1 gap-4 border-0 p-0 md:grid-cols-2">
        <Field
          label={t('hoster.webhookUrl')}
          value={webhookUrl}
          onChange={setWebhookUrl}
          type="url"
          placeholder="https://shop.example/hooks/msm"
        />
        <label className="space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('hoster.graceDays')}
          </span>
          <NumberStepper value={graceDays} onValueChange={setGraceDays} min={0} max={365} step={1} />
        </label>
        <label className="flex min-h-10 items-center justify-between gap-4 text-sm text-on-surface md:col-span-2">
          <span>{t('hoster.enabled')}</span>
          <Switch checked={enabled} onCheckedChange={setEnabled} />
        </label>
      </fieldset>
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" disabled={saving} onClick={onCancel}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={disabled || saving}>
          <Save className="h-4 w-4" aria-hidden="true" />{saving ? t('common.loading') : t('settings.save')}
        </Button>
      </div>
    </form>
  )
}

/**
 * Betreiberentscheidung: darf ein Server ohne eigene Zuordnung den panelweiten
 * Zugang mitbenutzen? Fuer Self-Hosted ist das der sinnvolle Default, fuer
 * einen Hoster in der Regel nicht — sonst liefe jeder Kundenserver mit den
 * zentralen Zugangsdaten des Betreibers.
 */
function PanelFallbackSection({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    credentialsApi
      .readPolicy()
      .then((policy) => { if (active) setAllowed(policy.allow_panel_fallback) })
      .catch(() => { if (active) setAllowed(null) })
    return () => { active = false }
  }, [])

  if (allowed === null) return null

  return (
    <div className="msm-card space-y-3 p-6">
      <h4 className="font-headline text-base font-semibold text-on-surface">
        {t('credentials.policy.title')}
      </h4>
      <p className="text-sm text-on-surface-variant">{t('credentials.policy.description')}</p>
      <label className="flex min-h-10 items-center justify-between gap-4 text-sm text-on-surface">
        <span>{t('credentials.policy.label')}</span>
        <Switch
          checked={allowed}
          disabled={!canWrite || busy}
          onCheckedChange={(next) => {
            if (!canWrite || busy) return
            setBusy(true)
            credentialsApi
              .updatePolicy(next)
              .then((policy) => {
                setAllowed(policy.allow_panel_fallback)
                toast.success(t('credentials.policy.saved'))
              })
              .catch((error: unknown) => {
                toast.error(
                  error instanceof SanitizedApiError
                    ? error.message
                    : t('credentials.errors.save'),
                )
              })
              .finally(() => setBusy(false))
          }}
        />
      </label>
    </div>
  )
}

function ProductSection({ integrationId, canWrite }: { integrationId: number; canWrite: boolean }) {
  const { t } = useTranslation()
  const [products, setProducts] = useState<HosterProduct[]>([])
  const [draft, setDraft] = useState<HosterProductWrite>(EMPTY_PRODUCT)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    hosterApi
      .listProducts(integrationId)
      .then(setProducts)
      .catch((error: unknown) => {
        toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.load'))
      })
  }, [integrationId, t])

  useEffect(load, [load])

  const save = async () => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      await hosterApi.saveProduct(integrationId, draft)
      setDraft(EMPTY_PRODUCT)
      load()
      toast.success(t('hoster.products.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="msm-card space-y-4 p-6">
      <h4 className="font-headline text-base font-semibold text-on-surface">{t('hoster.products.title')}</h4>
      <p className="text-sm text-on-surface-variant">{t('hoster.products.description')}</p>

      {products.length === 0 && (
        <p className="text-sm text-on-surface-variant">{t('hoster.products.empty')}</p>
      )}
      <ul className="space-y-2">
        {products.map((product) => (
          <li
            key={product.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-outline-variant/40 p-3 text-sm"
          >
            <span className="font-medium text-on-surface">{product.external_product_key}</span>
            <span className="text-on-surface-variant">
              {product.game_type} · {product.ram_limit_mb ?? '—'} MB · {product.cpu_limit_percent ?? '—'} %
            </span>
            {canWrite && (
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setBusy(true)
                  hosterApi
                    .deleteProduct(integrationId, product.id)
                    .then(() => { load(); toast.success(t('hoster.products.deleted')) })
                    .catch((error: unknown) => {
                      toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.delete'))
                    })
                    .finally(() => setBusy(false))
                }}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </Button>
            )}
          </li>
        ))}
      </ul>

      {canWrite && (
        <form
          className="grid grid-cols-1 gap-4 md:grid-cols-2"
          onSubmit={(event) => { event.preventDefault(); void save() }}
        >
          <Field
            label={t('hoster.products.key')}
            value={draft.external_product_key}
            onChange={(external_product_key) => setDraft({ ...draft, external_product_key })}
          />
          <Field
            label={t('hoster.products.gameType')}
            value={draft.game_type}
            onChange={(game_type) => setDraft({ ...draft, game_type })}
          />
          <label className="space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('hoster.products.ram')}
            </span>
            <NumberStepper
              value={draft.ram_limit_mb === null ? '' : String(draft.ram_limit_mb)}
              onValueChange={(value) => setDraft({ ...draft, ram_limit_mb: value ? Number(value) : null })}
              min={512}
              max={4194304}
              step={512}
            />
          </label>
          <label className="space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('hoster.products.cpu')}
            </span>
            <NumberStepper
              value={draft.cpu_limit_percent === null ? '' : String(draft.cpu_limit_percent)}
              onValueChange={(value) => setDraft({ ...draft, cpu_limit_percent: value ? Number(value) : null })}
              min={10}
              max={3200}
              step={10}
            />
          </label>
          <label className="space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('hoster.products.disk')}
            </span>
            <NumberStepper
              value={draft.disk_limit_gb === null ? '' : String(draft.disk_limit_gb)}
              onValueChange={(value) => setDraft({ ...draft, disk_limit_gb: value ? Number(value) : null })}
              min={1}
              max={1048576}
              step={1}
            />
          </label>
          <label className="space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('hoster.products.backupInterval')}
            </span>
            <NumberStepper
              value={draft.backup_interval_hours === null ? '' : String(draft.backup_interval_hours)}
              onValueChange={(value) => setDraft({ ...draft, backup_interval_hours: value ? Number(value) : null })}
              min={1}
              max={8760}
              step={1}
            />
          </label>
          <div className="md:col-span-2 flex items-center justify-between gap-4">
            <label className="flex items-center gap-3 text-sm text-on-surface">
              <span>{t('hoster.products.enabled')}</span>
              <Switch
                checked={draft.enabled}
                onCheckedChange={(enabled) => setDraft({ ...draft, enabled })}
              />
            </label>
            <Button
              type="submit"
              disabled={busy || !draft.external_product_key.trim() || !draft.game_type.trim()}
            >
              <Save className="h-4 w-4" aria-hidden="true" />{t('hoster.products.save')}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}

function ServiceSection({ integrationId }: { integrationId: number }) {
  const { t } = useTranslation()
  const [services, setServices] = useState<HosterService[]>([])

  useEffect(() => {
    let active = true
    hosterApi
      .listServices(integrationId)
      .then((rows) => { if (active) setServices(rows) })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.load'))
      })
    return () => { active = false }
  }, [integrationId, t])

  return (
    <div className="msm-card space-y-3 p-6">
      <h4 className="font-headline text-base font-semibold text-on-surface">{t('hoster.services.title')}</h4>
      {services.length === 0 && (
        <p className="text-sm text-on-surface-variant">{t('hoster.services.empty')}</p>
      )}
      <ul className="space-y-2">
        {services.map((service) => (
          <li
            key={service.external_service_id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-outline-variant/40 p-3 text-sm"
          >
            <span className="font-medium text-on-surface">{service.external_service_id}</span>
            <span className="text-on-surface-variant">
              {t(`hoster.states.${service.desired_state}`, service.desired_state)} → {service.status}
              {service.status_code ? ` (${service.status_code})` : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function DeliverySection({ integrationId, canWrite }: { integrationId: number; canWrite: boolean }) {
  const { t } = useTranslation()
  const [deliveries, setDeliveries] = useState<HosterDelivery[]>([])
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    hosterApi
      .listDeliveries(integrationId)
      .then(setDeliveries)
      .catch((error: unknown) => {
        toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.load'))
      })
  }, [integrationId, t])

  useEffect(load, [load])

  return (
    <div className="msm-card space-y-3 p-6">
      <h4 className="font-headline text-base font-semibold text-on-surface">{t('hoster.deliveries.title')}</h4>
      {deliveries.length === 0 && (
        <p className="text-sm text-on-surface-variant">{t('hoster.deliveries.empty')}</p>
      )}
      <ul className="space-y-2">
        {deliveries.map((delivery) => (
          <li
            key={delivery.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-outline-variant/40 p-3 text-sm"
          >
            <span className="font-medium text-on-surface">{delivery.event_type}</span>
            <span className="text-on-surface-variant">
              {delivery.status} · {t('hoster.deliveries.attempt', { count: delivery.attempt })}
              {delivery.response_code ? ` · HTTP ${delivery.response_code}` : ''}
            </span>
            {canWrite && delivery.status === 'failed' && (
              <Button
                type="button"
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  setBusy(true)
                  hosterApi
                    .retryDelivery(integrationId, delivery.id)
                    .then(() => { load(); toast.success(t('hoster.deliveries.retried')) })
                    .catch((error: unknown) => {
                      toast.error(error instanceof SanitizedApiError ? error.message : t('hoster.errors.save'))
                    })
                    .finally(() => setBusy(false))
                }}
              >
                <RefreshCw className="h-4 w-4" aria-hidden="true" />{t('hoster.deliveries.retry')}
              </Button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Field({
  label,
  value,
  onChange,
  className = '',
  ...props
}: {
  label: string
  value: string
  onChange: (value: string) => void
  className?: string
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  return (
    <label className={`space-y-1.5 ${className}`}>
      <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{label}</span>
      <input className="msm-input" value={value} onChange={(event) => onChange(event.target.value)} {...props} />
    </label>
  )
}
