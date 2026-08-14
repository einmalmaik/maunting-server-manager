import { useEffect, useId, useState } from 'react'
import { AlertCircle, CheckCircle2, KeyRound, PlugZap, Plus, RefreshCw, Save, Star, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  aiApi,
  type AiCatalogModel,
  type AiCostPolicy,
  type AiProviderAdmin,
  type AiProviderKind,
  type AiProviderTestResult,
  type AiProviderWrite,
} from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, Dropdown, Switch } from '@/Singra/UI'
import { eingabeInMicroUsd, microUsdInEingabe, preisFormatieren } from '@/utils/geld'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

interface ProviderDraft extends AiProviderWrite {
  id?: number
  operator_key_configured?: boolean
  operator_key_hint?: string | null
}

const EMPTY_PROVIDER: ProviderDraft = {
  name: '',
  provider_kind: '',
  default_model: '',
  enabled: true,
  requires_api_key: true,
  token_price_micro_usd_per_million: null,
  operator_api_key: '',
}

function toDraft(provider: AiProviderAdmin): ProviderDraft {
  return {
    id: provider.id,
    name: provider.name,
    provider_kind: provider.provider_kind,
    default_model: provider.default_model,
    enabled: provider.enabled,
    requires_api_key: provider.requires_api_key,
    token_price_micro_usd_per_million: provider.token_price_micro_usd_per_million,
    operator_api_key: '',
    operator_key_configured: provider.operator_key_configured,
    operator_key_hint: provider.operator_key_hint,
    // Ausdruecklich zuruecksetzen. `update()` **merged** in die vorhandene
    // Zeile, und was `toDraft` nicht nennt, ueberlebt das Speichern. Ohne diese
    // Zeile blieb die einmal gefasste Absicht „Key entfernen" stehen: das
    // Schluesselfeld war danach dauerhaft gesperrt (`disabled` haengt daran),
    // und der Umschalter zum Zuruecknehmen verschwand, weil er nur bei
    // `operator_key_configured` erscheint — das der Server gerade auf `false`
    // gesetzt hatte. Der Provider liess sich dann nicht mehr mit einem
    // Schluessel versehen, ohne die Seite neu zu laden.
    //
    // „Ich will loeschen" ist eine Absicht fuer genau einen Speichervorgang,
    // kein Zustand des Providers.
    clear_operator_api_key: false,
  }
}

export function AiProvidersSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [providers, setProviders] = useState<ProviderDraft[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | 'new' | null>(null)
  const [kinds, setKinds] = useState<AiProviderKind[]>([])
  // Waehrung und Kurs fuer das Preisfeld. Einmal fuer die ganze Seite und
  // nicht je Formular: die Politik ist panelweit, und drei Anbieter wuerden
  // sonst dreimal dasselbe fragen.
  const [costPolicy, setCostPolicy] = useState<AiCostPolicy | null>(null)

  // Die Anbieterliste ist statisch und kommt aus `ai_provider_registry` — ein
  // Abruf fuer die ganze Seite, nicht einer je Formular.
  useEffect(() => {
    let active = true
    aiApi.listProviderKinds()
      .then((rows) => { if (active) setKinds(rows) })
      .catch(() => undefined)
    aiApi.getCostPolicy()
      .then((policy) => { if (active) setCostPolicy(policy) })
      .catch(() => undefined)
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    aiApi.listProviderSettings()
      .then((rows) => { if (active) setProviders(rows.map(toDraft)) })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.providers.errors.load'))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  const update = (index: number, patch: Partial<ProviderDraft>) => {
    setProviders((current) => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...patch } : item
    )))
  }

  const save = async (draft: ProviderDraft, index?: number) => {
    const target = draft.id ?? 'new'
    if (!canWrite || busyId !== null) return
    setBusyId(target)
    const payload: AiProviderWrite = {
      name: draft.name.trim(),
      provider_kind: draft.provider_kind,
      default_model: draft.default_model.trim(),
      enabled: draft.enabled,
      requires_api_key: draft.requires_api_key,
      token_price_micro_usd_per_million: draft.token_price_micro_usd_per_million ?? null,
      ...(draft.operator_api_key ? { operator_api_key: draft.operator_api_key } : {}),
      ...(draft.clear_operator_api_key ? { clear_operator_api_key: true } : {}),
    }
    try {
      const saved = draft.id
        ? await aiApi.updateProvider(draft.id, payload)
        : await aiApi.createProvider(payload)
      if (index === undefined) {
        setProviders((current) => [...current, toDraft(saved)])
        setCreating(false)
      } else {
        update(index, toDraft(saved))
      }
      toast.success(t('ai.providers.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.providers.errors.save'))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (provider: ProviderDraft) => {
    if (!provider.id || !canWrite || busyId !== null) return
    const accepted = await confirm({
      title: t('ai.providers.deleteTitle'),
      message: t('ai.providers.deleteConfirm', { name: provider.name }),
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!accepted) return
    setBusyId(provider.id)
    try {
      await aiApi.deleteProvider(provider.id)
      setProviders((current) => current.filter((item) => item.id !== provider.id))
      toast.success(t('ai.providers.deleted'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.providers.errors.delete'))
    } finally {
      setBusyId(null)
    }
  }

  if (loading) {
    return <div className="flex h-32 items-center justify-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
  }

  return (
    <section className="space-y-4" aria-labelledby="ai-provider-title">
      <div className="msm-card flex flex-wrap items-start justify-between gap-4 p-6">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h3 id="ai-provider-title" className="font-headline text-lg font-semibold text-on-surface">{t('ai.providers.title')}</h3>
          </div>
          <p className="mt-2 text-sm text-on-surface-variant">{t('ai.providers.description')}</p>
        </div>
        {canWrite && !creating && (
          <Button type="button" variant="secondary" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" aria-hidden="true" />{t('ai.providers.add')}
          </Button>
        )}
      </div>

      {providers.map((provider, index) => (
        <ProviderForm
          key={provider.id}
          draft={provider}
          kinds={kinds}
          costPolicy={costPolicy}
          disabled={!canWrite || busyId !== null}
          saving={busyId === provider.id}
          onChange={(patch) => update(index, patch)}
          onSave={() => void save(provider, index)}
          onDelete={() => void remove(provider)}
          onTest={provider.id === undefined ? undefined : () => aiApi.testProvider(provider.id as number)}
        />
      ))}
      {providers.length === 0 && !creating && (
        <div className="msm-card p-6 text-sm text-on-surface-variant">{t('ai.providers.empty')}</div>
      )}
      {creating && (
        <ProviderForm
          draft={{ ...EMPTY_PROVIDER, provider_kind: kinds[0]?.kind ?? '' }}
          kinds={kinds}
          costPolicy={costPolicy}
          disabled={busyId !== null}
          saving={busyId === 'new'}
          onChange={() => undefined}
          localDraft
          onSaveDraft={(draft) => void save(draft)}
          onCancel={() => setCreating(false)}
        />
      )}
    </section>
  )
}

function ProviderForm({
  draft: initialDraft,
  kinds,
  costPolicy,
  disabled,
  saving,
  localDraft = false,
  onChange,
  onSave,
  onSaveDraft,
  onDelete,
  onCancel,
  onTest,
}: {
  draft: ProviderDraft
  kinds: AiProviderKind[]
  /** `null`, solange die Politik nicht geladen ist — dann gilt USD 1:1. */
  costPolicy: AiCostPolicy | null
  disabled: boolean
  saving: boolean
  localDraft?: boolean
  onChange: (patch: Partial<ProviderDraft>) => void
  onSave?: () => void
  onSaveDraft?: (draft: ProviderDraft) => void
  onDelete?: () => void
  onCancel?: () => void
  onTest?: () => Promise<AiProviderTestResult>
}) {
  const { t, i18n } = useTranslation()
  const [local, setLocal] = useState<ProviderDraft>({ ...initialDraft })
  const [testResult, setTestResult] = useState<AiProviderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [models, setModels] = useState<AiCatalogModel[] | null>(null)
  const [loadingModels, setLoadingModels] = useState(false)
  const draft = localDraft ? local : initialDraft
  const spec = kinds.find((item) => item.kind === draft.provider_kind) ?? null

  /**
   * Der Modellkatalog des gewaehlten Anbieters.
   *
   * `null` heisst „noch nicht geladen oder nicht erreichbar" — dann bleibt das
   * Modell ein Textfeld statt einer Auswahl. Ein leeres Dropdown waere die
   * schlechtere Antwort: der Betreiber koennte nichts mehr eintragen, obwohl
   * sein Modell existiert.
   */
  const ladeModelle = async (refresh = false) => {
    if (!draft.provider_kind || loadingModels) return
    setLoadingModels(true)
    try {
      setModels(await aiApi.listCatalogModels(draft.provider_kind, refresh))
    } catch {
      setModels(null)
    } finally {
      setLoadingModels(false)
    }
  }

  useEffect(() => {
    let active = true
    if (!draft.provider_kind) { setModels(null); return }
    setLoadingModels(true)
    aiApi.listCatalogModels(draft.provider_kind)
      .then((rows) => { if (active) setModels(rows) })
      .catch(() => { if (active) setModels(null) })
      .finally(() => { if (active) setLoadingModels(false) })
    return () => { active = false }
  }, [draft.provider_kind])

  const gewaehltesModell = models?.find((item) => item.model_id === draft.default_model) ?? null
  // Kommt aus dem Katalog, nicht aus der Oberflaeche: fuehrt der Anbieter die
  // empfohlene Kennung nicht mehr, gibt es hier `null` und die Empfehlung
  // verschwindet von selbst — statt auf ein Modell zu zeigen, das es nicht gibt.
  const empfohlenesModell = models?.find((item) => item.recommended) ?? null

  // Unser `Dropdown` ist ein Knopf, kein `<select>`. Ein umschliessendes
  // `<label>` beschriftet ihn deshalb nicht — es braucht `htmlFor` und eine ID,
  // die auch bei zwei Formularen auf derselben Seite eindeutig bleibt.
  const kindId = useId()
  const modelId = useId()
  const preisId = useId()

  // Solange die Politik nicht geladen ist, gilt USD 1:1 — die Waehrung der
  // Buchung. Ein Rueckfall auf Euro wuerde einen getippten Preis stillschweigend
  // durch einen Kurs teilen, den es an dieser Stelle noch gar nicht gab.
  const waehrung = costPolicy ?? { currency: 'USD', usd_rate: '1' }
  const preisMicro = draft.token_price_micro_usd_per_million ?? null
  // Eigener Zustand fuer das Feld: waehrend jemand „1," tippt, ist die Eingabe
  // noch keine Zahl. Wuerde sie sofort durch den Umrechner laufen, spraenge der
  // Cursor beim dritten Zeichen. Uebernommen wird beim Verlassen des Feldes.
  const [preisText, setPreisText] = useState(() => microUsdInEingabe(preisMicro, waehrung))
  // Kommt der Wert von aussen — nach dem Speichern, oder wenn die Politik
  // nachlaedt —, folgt das Feld. Der Vergleich verhindert, dass es das auch
  // waehrend des Tippens tut.
  useEffect(() => {
    const frisch = microUsdInEingabe(preisMicro, waehrung)
    setPreisText((aktuell) => (
      eingabeInMicroUsd(aktuell, waehrung) === preisMicro ? aktuell : frisch
    ))
  }, [preisMicro, waehrung.currency, waehrung.usd_rate])

  /**
   * Schickt eine echte Mini-Anfrage an den Anbieter.
   *
   * Ohne das ist eine Fehlkonfiguration erst im Chat sichtbar — und dort sehen
   * eine falsche Basis-URL, ein Tippfehler im Modellnamen und ein abgelaufener
   * Key alle gleich aus.
   */
  const runTest = async () => {
    if (!onTest || testing) return
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await onTest())
    } catch (error: unknown) {
      setTestResult({
        ok: false,
        code: null,
        detail: error instanceof Error ? error.message : null,
      })
    } finally {
      setTesting(false)
    }
  }
  const change = (patch: Partial<ProviderDraft>) => {
    if (localDraft) setLocal((current) => ({ ...current, ...patch }))
    else onChange(patch)
  }
  const valid = Boolean(draft.name.trim() && draft.provider_kind && draft.default_model.trim())

  return (
    <form className="msm-card space-y-5 p-6" onSubmit={(event) => {
      event.preventDefault()
      if (localDraft) onSaveDraft?.(draft)
      else onSave?.()
    }}>
      <fieldset disabled={disabled} className="grid grid-cols-1 gap-4 border-0 p-0 md:grid-cols-2">
        <ProviderInput label={t('ai.providers.name')} value={draft.name} onChange={(name) => change({ name })} />
        <div className="space-y-1.5">
          <label htmlFor={kindId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('ai.providers.kind')}
          </label>
          <Dropdown
            id={kindId}
            value={draft.provider_kind || null}
            onChange={(kind) => change({ provider_kind: kind, default_model: '' })}
            // Eine Zeile ohne bekannten Anbieter gibt es nur nach der Migration
            // 20260811_01, die fremde Zugaenge geparkt hat. Der Platzhalter sagt
            // das, statt stillschweigend auf den ersten Anbieter umzustellen —
            // und er ist bewusst **keine** waehlbare Option: „kein Anbieter" ist
            // ein Befund, keine Einstellung.
            placeholder={t('ai.providers.kindMissing')}
            options={kinds.map((item) => ({ value: item.kind, label: item.label }))}
          />
          {spec && (
            <p className="text-xs text-on-surface-variant">
              {t('ai.providers.kindHint', { url: spec.base_url })}
              {' '}
              <a href={spec.key_url} target="_blank" rel="noreferrer" className="underline">{t('ai.providers.keyLink')}</a>
            </p>
          )}
          {!draft.provider_kind && (
            <p className="text-xs text-status-error">{t('ai.providers.kindMissingHint')}</p>
          )}
        </div>
        <div className="md:col-span-2">
          <div className="flex items-end gap-2">
            <div className="flex-1">
              {/* Ausgewaehlt statt getippt: ein Tippfehler fiel bisher erst beim
                  Testaufruf auf, und ueber die Denkfaehigkeiten des Modells
                  wusste MSM so oder so nichts. */}
              {models && models.length > 0 ? (
                <div className="space-y-1.5">
                  <label htmlFor={modelId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('ai.providers.model')}
                  </label>
                  <Dropdown
                    id={modelId}
                    value={draft.default_model || null}
                    onChange={(default_model) => change({ default_model })}
                    placeholder={t('ai.providers.modelChoose')}
                    // Das empfohlene Modell steht oben. Bei ueber 400 Eintraegen
                    // ist eine Empfehlung, die man erst suchen muss, keine.
                    options={[...models]
                      .sort((a, b) => Number(b.recommended) - Number(a.recommended))
                      .map((item) => ({
                        value: item.model_id,
                        // Der Anzeigename daneben: `anthropic/claude-opus-5` ist
                        // die ID, die gespeichert wird, „Claude Opus 5" das, was
                        // der Betreiber sucht.
                        label: item.model_id,
                        hint: item.recommended
                          ? [item.name !== item.model_id ? item.name : null, t('ai.providers.recommended')]
                              .filter(Boolean)
                              .join(' · ')
                          : item.name !== item.model_id
                            ? item.name
                            : undefined,
                        icon: item.recommended
                          ? <Star className="h-3.5 w-3.5 fill-current text-primary" aria-hidden="true" />
                          : undefined,
                      }))}
                  />
                </div>
              ) : (
                <ProviderInput
                  label={t('ai.providers.model')}
                  value={draft.default_model}
                  onChange={(default_model) => change({ default_model })}
                />
              )}
            </div>
            <Button type="button" variant="ghost" disabled={!draft.provider_kind || loadingModels} onClick={() => void ladeModelle(true)}>
              <RefreshCw className={`h-4 w-4 ${loadingModels ? 'animate-spin' : ''}`} aria-hidden="true" />
              <span className="sr-only">{t('ai.providers.reloadModels')}</span>
            </Button>
          </div>
          {models === null && !loadingModels && draft.provider_kind && (
            <p className="msm-field-help">{t('ai.providers.catalogUnavailable')}</p>
          )}
          {/* Steht nur da, solange der Betreiber die Empfehlung nicht gewaehlt
              hat. Danach waere es eine Belehrung ueber eine Entscheidung, die
              schon gefallen ist. */}
          {empfohlenesModell && draft.default_model !== empfohlenesModell.model_id && (
            <p className="msm-field-help flex items-start gap-1.5">
              <Star className="mt-0.5 h-3.5 w-3.5 shrink-0 fill-current text-primary" aria-hidden="true" />
              <span>
                {t('ai.providers.recommendationHint', { model: empfohlenesModell.model_id })}{' '}
                <button
                  type="button"
                  onClick={() => change({ default_model: empfohlenesModell.model_id })}
                  className="underline underline-offset-2 hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {t('ai.providers.recommendationApply')}
                </button>
              </span>
            </p>
          )}
          {gewaehltesModell && <ModelCapabilities model={gewaehltesModell} />}
        </div>
        <ProviderInput
          className="md:col-span-2"
          type="password"
          autoComplete="new-password"
          label={t('ai.providers.operatorKey')}
          value={draft.operator_api_key ?? ''}
          placeholder={draft.operator_key_configured ? t('ai.providers.keyConfigured', { hint: draft.operator_key_hint ?? '••••' }) : t('ai.providers.keyOptional')}
          disabled={draft.clear_operator_api_key}
          onChange={(operator_api_key) => change({ operator_api_key, clear_operator_api_key: false })}
        />
        {draft.operator_key_configured && (
          <div className="md:col-span-2">
            <Toggle label={t('ai.providers.clearOperatorKey')} checked={Boolean(draft.clear_operator_api_key)} onChange={(clear_operator_api_key) => change({ clear_operator_api_key, operator_api_key: '' })} />
          </div>
        )}
        <Toggle label={t('ai.providers.enabled')} checked={draft.enabled} onChange={(enabled) => change({ enabled })} />
        <Toggle label={t('ai.providers.requiresKey')} checked={draft.requires_api_key} onChange={(requires_api_key) => change({ requires_api_key })} />
        <div className="md:col-span-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
          {/* Ein freies Textfeld und kein `NumberStepper` mehr. Der Stepper
              zaehlte in ganzen Schritten, und genau daran scheiterte die
              Eingabe: zwischen 1 und 2 Cent lag nichts, „1,20 €" war nicht
              eintragbar. Ein Preis ist eine Dezimalzahl, kein Zaehler. */}
          <div className="space-y-1.5">
            <label htmlFor={preisId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.providers.tokenPrice', { currency: waehrung.currency })}
            </label>
            <input
              id={preisId}
              type="text"
              inputMode="decimal"
              className="msm-input w-full"
              placeholder="1,20"
              value={preisText}
              onChange={(event) => setPreisText(event.target.value)}
              onBlur={() => change({
                token_price_micro_usd_per_million: eingabeInMicroUsd(preisText, waehrung),
              })}
              aria-label={t('ai.providers.tokenPrice', { currency: waehrung.currency })}
            />
          </div>
          {/* Was daraus gespeichert wird, sichtbar und nicht im Verborgenen.
              Gebucht wird in Dollar; eine Umrechnung, die der Betreiber nicht
              sieht, waere spaeter der erste Verdaechtige, wenn eine Zahl nicht
              stimmt. */}
          {waehrung.currency !== 'USD' && preisMicro !== null && (
            <p className="msm-field-help">
              {t('ai.providers.tokenPriceConverted', {
                amount: preisFormatieren(preisMicro, 'USD', i18n.language),
              })}
            </p>
          )}
          <p className="msm-field-help">{t('ai.providers.tokenPriceHint')}</p>
        </div>
      </fieldset>
      {testResult && (
        <p
          className={`flex items-start gap-2 rounded-lg border p-3 text-xs leading-5 ${
            testResult.ok
              ? 'border-status-success/30 bg-status-success/10 text-status-success'
              : 'border-status-error/30 bg-status-error/10 text-status-error'
          }`}
          role="status"
        >
          {testResult.ok
            ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />}
          <span>
            {testResult.ok
              ? t('ai.providers.testOk')
              : t(`ai.errors.codes.${testResult.code}`, { defaultValue: t('ai.providers.testFailed') })}
            {/* Die Anbietermeldung im Original: sie benennt die Ursache
                praeziser, als ein uebersetzter Code es je koennte. */}
            {testResult.detail && <span className="mt-1 block font-mono text-[11px] opacity-80">{testResult.detail}</span>}
          </span>
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        {onDelete && <Button type="button" variant="destructive" disabled={disabled} onClick={onDelete}><Trash2 className="h-4 w-4" />{t('common.delete')}</Button>}
        {onTest && (
          <Button type="button" variant="secondary" disabled={disabled || testing} onClick={() => void runTest()}>
            <PlugZap className="h-4 w-4" />
            {testing ? t('common.loading') : t('ai.providers.test')}
          </Button>
        )}
        {onCancel && <Button type="button" variant="ghost" disabled={disabled} onClick={onCancel}>{t('common.cancel')}</Button>}
        <Button type="submit" disabled={disabled || !valid}><Save className="h-4 w-4" />{saving ? t('common.loading') : t('settings.save')}</Button>
      </div>
    </form>
  )
}

/**
 * Was das gewaehlte Modell kann — direkt aus dem Katalog des Anbieters.
 *
 * Der Betreiber soll vor dem Speichern sehen, worauf er sich einlaesst. Drei
 * Faelle, die sich wirklich unterscheiden und gemessen alle haeufig sind:
 * Stufen (127 von 402 Modellen), nur an/aus (145) und nicht abschaltbar (82).
 */
function ModelCapabilities({ model }: { model: AiCatalogModel }) {
  const { t } = useTranslation()
  if (!model.reasoning) {
    return <p className="mt-2 text-xs text-on-surface-variant">{t('ai.providers.caps.none')}</p>
  }
  return (
    <div className="mt-2 space-y-1 text-xs text-on-surface-variant">
      {model.efforts.length > 0 ? (
        <p>
          {t('ai.providers.caps.levels')}{' '}
          {model.efforts.map((effort) => (
            <span key={effort} className="mr-1 inline-block rounded-md border border-outline-variant/50 px-1.5 py-0.5 font-mono text-[11px]">
              {t(`ai.reasoning.levels.${effort}`, { defaultValue: effort })}
            </span>
          ))}
        </p>
      ) : (
        <p>{t('ai.providers.caps.toggleOnly')}</p>
      )}
      {model.mandatory && <p className="text-status-warning">{t('ai.providers.caps.mandatory')}</p>}
    </div>
  )
}

function ProviderInput({ label, value, onChange, className = '', ...props }: {
  label: string
  value: string
  onChange: (value: string) => void
  className?: string
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  return <label className={`space-y-1.5 ${className}`}><span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">{label}</span><input className="msm-input" value={value} onChange={(event) => onChange(event.target.value)} {...props} /></label>
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex min-h-10 items-center justify-between gap-4 text-sm text-on-surface"><span>{label}</span><Switch checked={checked} onCheckedChange={onChange} /></label>
}
