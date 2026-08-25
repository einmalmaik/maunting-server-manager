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

/**
 * Der Auswahlwert für „kein Worker-Modell" bzw. „nicht nachdenken".
 *
 * Unser `Dropdown` trägt Zeichenketten; `null` wäre dort „nichts gewählt" und
 * zeigte den Platzhalter statt einer Entscheidung — dasselbe Muster wie
 * `AUS`/`AN_OHNE_STUFE` im Chat-Kopf. Der Wert kann keine echte Kennung
 * verdecken: kein Anbieter führt ein Modell dieses Namens.
 */
const KEIN_WORKER = '__aus__'
const KEINE_ETHICS = '__aus__'

/**
 * Der Hinweis unter einer Modellzeile: Anzeigename, Empfehlung, Bildsicht.
 *
 * „Sieht Bilder" steht hier und in keinem Chat. Kann die KI nicht hinsehen,
 * sagt sie das dort als eine Fähigkeit, die ihr gerade fehlt, und nicht als
 * Eigenschaft eines Modells (`ai_stream_service.KEIN_BLICK_GRUND`). Der
 * technische Grund gehört an die Stelle, an der man ihn beheben kann — also
 * dorthin, wo das Modell gewählt wird.
 *
 * `vision === null` heißt „der Katalog sagt nichts dazu". Dann steht auch
 * hier nichts: eine Marke wäre eine Behauptung, ihr Fehlen ist nur Schweigen.
 */
function modellHinweis(
  item: AiCatalogModel,
  t: (schluessel: string) => string,
): string | undefined {
  const teile = [
    item.name !== item.model_id ? item.name : null,
    item.recommended ? t('ai.providers.recommended') : null,
    item.vision ? t('ai.providers.vision') : null,
  ].filter(Boolean)
  return teile.length > 0 ? teile.join(' · ') : undefined
}

const EMPTY_PROVIDER: ProviderDraft = {
  name: '',
  provider_kind: '',
  default_model: '',
  enabled: true,
  requires_api_key: true,
  token_price_micro_usd_per_million: null,
  // Keine Vorbelegung, bei beiden. Es gibt weder eine Standardstimme noch ein
  // Standard-Hoermodell — MSM kennt die Stimmen des fremden Kontos nicht, und
  // ein geratenes Hoermodell stuende auf der Rechnung des Betreibers.
  default_voice: null,
  transcription_model: null,
  // Ohne Worker-Modell gilt der heutige Ein-Modell-Betrieb — der dokumentierte
  // Fallback (docs/agentic-framework.md, §5), keine Pflichtangabe.
  worker_model: null,
  worker_reasoning_effort: null,
  ethics_model: null,
  ethics_reasoning_effort: null,
  ethics_mode: 'auto',
  // Nur Anbieter mit `ressource_noetig` brauchen ihn; ohne Vorbelegung, weil
  // MSM die Ressourcen des fremden Kontos nicht kennt.
  azure_resource_name: null,
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
    default_voice: provider.default_voice,
    transcription_model: provider.transcription_model,
    worker_model: provider.worker_model,
    worker_reasoning_effort: provider.worker_reasoning_effort,
    ethics_model: provider.ethics_model,
    ethics_reasoning_effort: provider.ethics_reasoning_effort,
    ethics_mode: provider.ethics_mode || 'auto',
    azure_resource_name: provider.azure_resource_name,
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
    // Jedes Zusatzfeld geht nur mit seinem Zugang mit — dieselbe Bedingung,
    // unter der es ueberhaupt erscheint. Der jeweils andere schickt es gar
    // nicht erst: sonst stuende in seiner Zeile eine Angabe, die er nie
    // verwendet, und beim naechsten Blick in die Datenbank saehe es aus wie
    // eine Einstellung.
    const gewaehlt = kinds.find((item) => item.kind === draft.provider_kind)
    const protokoll = gewaehlt?.protokoll
    const payload: AiProviderWrite = {
      name: draft.name.trim(),
      provider_kind: draft.provider_kind,
      default_model: draft.default_model?.trim() || null,
      enabled: draft.enabled,
      requires_api_key: draft.requires_api_key,
      token_price_micro_usd_per_million: draft.token_price_micro_usd_per_million ?? null,
      ...(protokoll === 'tts' ? { default_voice: draft.default_voice?.trim() || null } : {}),
      ...(protokoll === 'chat_completions'
        ? {
            // Kann der Anbieter nicht zuhoeren, zeigt das Formular das Feld
            // nicht — dann darf auch kein alter Wert stehen bleiben, den
            // niemand mehr sehen und keiner mehr loeschen kann.
            transcription_model: gewaehlt?.kann_hoeren
              ? draft.transcription_model?.trim() || null
              : null,
            worker_model: draft.worker_model?.trim() || null,
            // Eine Denkstufe ohne Arbeitsmodell ist keine Einstellung — sie
            // geht mit dem Modell und faellt mit ihm.
            worker_reasoning_effort: draft.worker_model?.trim()
              ? draft.worker_reasoning_effort || null
              : null,
            ethics_model: draft.ethics_model?.trim() || null,
            ethics_reasoning_effort: draft.ethics_model?.trim()
              ? draft.ethics_reasoning_effort || null
              : null,
            ethics_mode: draft.ethics_mode || 'auto',
          }
        : {}),
      // Wie die Felder darueber: nur mit dem Zugang, der ihn braucht. Ein
      // mitgeschickter `null` an einem Anbieter ohne Ressource waere zwar
      // harmlos, stuende aber als Einstellung in seiner Zeile.
      ...(gewaehlt?.ressource_noetig
        ? { azure_resource_name: draft.azure_resource_name?.trim() || null }
        : {}),
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
  // Als eigener Wert und nicht als `spec?.…` in der Abhaengigkeitsliste: `spec`
  // ist bei jedem Rendern ein neues Objekt und loeste den Effekt endlos aus.
  // `null` heisst „Anbieterliste noch nicht geladen" und nicht „fuehrt keinen".
  const fuehrtKatalog = spec?.fuehrt_katalog ?? null
  // Die Adresse, wie der Betreiber sie sehen soll. Traegt `base_url` eine
  // Vorlage, steht dort sein eigener Name statt des rohen `{ressource}` —
  // solange er noch keinen eingetragen hat, das Beispiel aus dem Platzhalter.
  // Ohne Vorlage laeuft `replace` ins Leere, deshalb ein Ausdruck fuer alle.
  const adresse = spec
    ? spec.base_url.replace(
        '{ressource}',
        draft.azure_resource_name?.trim() || t('ai.providers.azureResourcePlaceholder'),
      )
    : ''

  /**
   * Der Modellkatalog des gewaehlten Anbieters.
   *
   * `null` heisst „noch nicht geladen oder nicht erreichbar" — dann bleibt das
   * Modell ein Textfeld statt einer Auswahl. Ein leeres Dropdown waere die
   * schlechtere Antwort: der Betreiber koennte nichts mehr eintragen, obwohl
   * sein Modell existiert.
   *
   * Bei einem Anbieter **ohne** Katalog wird gar nicht erst gefragt. Die
   * Antwort waere immer die leere Liste, und der Hinweis darunter sagt bereits,
   * warum — ein Abruf dafuer waere eine Ladeanzeige ohne Ergebnis.
   */
  const ladeModelle = async (refresh = false) => {
    if (!draft.provider_kind || loadingModels) return
    if (spec && !spec.fuehrt_katalog) return
    setLoadingModels(true)
    try {
      setModels(await aiApi.listCatalogModels(draft.provider_kind, refresh, draft.id))
    } catch {
      setModels(null)
    } finally {
      setLoadingModels(false)
    }
  }

  useEffect(() => {
    let active = true
    if (!draft.provider_kind) { setModels(null); return }
    // Ein Anbieter ohne Katalog wird nicht gefragt — siehe `ladeModelle`.
    // ``[]`` und nicht ``null``: „führt keine Liste" ist eine Auskunft, „noch
    // nicht geladen" ein Zustand, und nur der zweite darf eine Ladeanzeige
    // rechtfertigen.
    if (fuehrtKatalog === false) { setModels([]); return }
    setLoadingModels(true)
    // `draft.id` geht mit, weil manche Anbieter ihren Katalog nur gegen den
    // Schlüssel herausgeben. Beim Anlegen gibt es die Kennung noch nicht — dann
    // kommt eine leere Liste, und der Hinweis darunter erklärt die Reihenfolge.
    aiApi.listCatalogModels(draft.provider_kind, false, draft.id)
      .then((rows) => { if (active) setModels(rows) })
      .catch(() => { if (active) setModels(null) })
      .finally(() => { if (active) setLoadingModels(false) })
    return () => { active = false }
  }, [draft.provider_kind, draft.id, fuehrtKatalog])

  /*
   * Anbieter ohne Katalog: die eingetippten Kennungen einzeln nachschlagen.
   *
   * Sonst bliebe bei Azure die Auswahl der Worker- oder Ethik-Denkstufe leer — sie haengt
   * an der Katalogliste, und die ist dort leer.
   *
   * Nur bei `fuehrt_katalog === false`. Ein Anbieter **mit** Katalog behaelt
   * seine Liste als einzige Wahrheit — sonst zeigte das Formular Stufen zu
   * einem Modell, das seine Liste nicht fuehrt.
   */
  const [einzelmodelle, setEinzelmodelle] = useState<Record<string, AiCatalogModel | null>>({})
  const kennungen = [draft.default_model?.trim(), draft.worker_model?.trim(), draft.ethics_model?.trim()]
    .filter((wert): wert is string => Boolean(wert))
    .join(' ')
  useEffect(() => {
    if (fuehrtKatalog !== false || !draft.provider_kind || !kennungen) return
    let active = true
    // Kurz warten: die Kennung ist ein Textfeld, und ohne diese Pause liefe je
    // Tastendruck eine Anfrage.
    const timer = window.setTimeout(() => {
      void Promise.all(
        kennungen.split(' ').map((kennung) =>
          aiApi.findCatalogModel(draft.provider_kind, kennung)
            .then((modell) => [kennung, modell] as const)
            .catch(() => [kennung, null] as const),
        ),
      ).then((paare) => {
        if (active) setEinzelmodelle((alt) => ({ ...alt, ...Object.fromEntries(paare) }))
      })
    }, 400)
    return () => { active = false; window.clearTimeout(timer) }
  }, [draft.provider_kind, fuehrtKatalog, kennungen])

  const nachgeschlagen = (kennung: string | null | undefined): AiCatalogModel | null =>
    (kennung?.trim() ? einzelmodelle[kennung.trim()] : null) ?? null

  const gewaehltesModell = models?.find((item) => item.model_id === draft.default_model)
    ?? nachgeschlagen(draft.default_model)
  // Das Arbeitsmodell der Worker — aus demselben Katalog. `null`, wenn keines
  // gewaehlt ist oder der Katalog es nicht (mehr) fuehrt; die Stufenwahl
  // verschwindet dann mit, denn Stufen kommen immer aus dem Katalog.
  const workerModell = models?.find((item) => item.model_id === draft.worker_model)
    ?? nachgeschlagen(draft.worker_model)
  // Das Modell der Ethics Engine — ebenfalls aus demselben Katalog.
  const ethicsModell = models?.find((item) => item.model_id === draft.ethics_model)
    ?? nachgeschlagen(draft.ethics_model)
  // Kommt aus dem Katalog, nicht aus der Oberflaeche: fuehrt der Anbieter die
  // empfohlene Kennung nicht mehr, gibt es hier `null` und die Empfehlung
  // verschwindet von selbst — statt auf ein Modell zu zeigen, das es nicht gibt.
  const empfohlenesModell = models?.find((item) => item.recommended) ?? null

  const keyId = useId()
  const kindId = useId()
  const modelId = useId()
  const stimmeId = useId()
  const hoerenId = useId()
  const preisId = useId()
  const workerModellId = useId()
  const workerStufeId = useId()
  const ethicsModellId = useId()
  const ethicsStufeId = useId()
  const ethicsModusId = useId()
  const ttsModellId = useId()
  const ressourceId = useId()

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
  const valid = Boolean(
    draft.name.trim() &&
    draft.provider_kind &&
    (draft.default_model?.trim() || draft.transcription_model?.trim() || draft.default_voice?.trim()) &&
    // Ein Azure-Zugang ohne Ressourcennamen hat keine Adresse. Der Server
    // lehnt ihn ohnehin ab; hier gesperrt, damit der Betreiber die Absage
    // nicht erst nach dem Klick liest.
    (!spec?.ressource_noetig || Boolean(draft.azure_resource_name?.trim()))
  )

  return (
    <form className="msm-card space-y-6 p-6" onSubmit={(event) => {
      event.preventDefault()
      if (localDraft) onSaveDraft?.(draft)
      else onSave?.()
    }}>
      <fieldset disabled={disabled} className="space-y-5 border-0 p-0">
        {/* Name und Anbietertyp */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <ProviderInput label={t('ai.providers.name')} value={draft.name} onChange={(name) => change({ name })} />
          <div className="space-y-1.5">
            <label htmlFor={kindId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.providers.kind')}
            </label>
            <Dropdown
              id={kindId}
              value={draft.provider_kind || null}
              onChange={(kind) => change({ provider_kind: kind, default_model: '' })}
              placeholder={t('ai.providers.kindMissing')}
              options={kinds.map((item) => ({
                value: item.kind,
                label: item.label,
                // Ein Chatanbieter ohne Gehoer bekommt den kuerzeren Hinweis.
                // „Chat und Gehoer" waere bei Azure ein Versprechen, das der
                // Sprachmodus nicht einloest.
                hint: t(
                  item.protokoll === 'chat_completions' && !item.kann_hoeren
                    ? 'ai.providers.protokoll.chat_only'
                    : `ai.providers.protokoll.${item.protokoll}`,
                ),
              }))}
            />
            {spec && (
              <p className="text-xs text-on-surface-variant">
                {t('ai.providers.kindHint', { url: adresse })}
                {' '}
                <a href={spec.key_url} target="_blank" rel="noreferrer" className="underline">{t('ai.providers.keyLink')}</a>
              </p>
            )}
            {spec?.protokoll === 'tts' && (
              <p className="text-xs text-on-surface-variant">{t('ai.providers.ttsHint')}</p>
            )}
            {!draft.provider_kind && (
              <p className="text-xs text-status-error">{t('ai.providers.kindMissingHint')}</p>
            )}
          </div>
        </div>

        {/* Ressourcenname — das eine Stück Adresse, das MSM nicht selbst weiss.
            Erscheint nur, wenn der gewählte Anbieter es angemeldet hat
            (`ressource_noetig`); ein Vergleich auf einen Anbieternamen stünde
            hier als zweite Registry und liefe irgendwann auseinander. */}
        {spec?.ressource_noetig && (
          <div className="space-y-1.5 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
            <label htmlFor={ressourceId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.providers.azureResource')}
            </label>
            <input
              id={ressourceId}
              type="text"
              className="msm-input w-full"
              autoComplete="off"
              spellCheck={false}
              value={draft.azure_resource_name ?? ''}
              onChange={(ereignis) => change({ azure_resource_name: ereignis.target.value })}
              placeholder={t('ai.providers.azureResourcePlaceholder')}
              aria-label={t('ai.providers.azureResource')}
            />
            <p className="msm-field-help">
              {t('ai.providers.azureResourceHint', { url: adresse })}
            </p>
            {!draft.azure_resource_name?.trim() && (
              <p className="text-xs text-status-error">{t('ai.providers.azureResourceMissing')}</p>
            )}
          </div>
        )}

        {/* API-Key */}
        <div className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4 space-y-2">
          <div className="flex items-center justify-between">
            <label htmlFor={keyId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.providers.operatorKey')}
            </label>
            {draft.clear_operator_api_key ? (
              <span className="text-xs text-status-warning flex items-center gap-1.5">
                {t('ai.providers.keyWillBeCleared')}
                <button
                  type="button"
                  onClick={() => change({ clear_operator_api_key: false })}
                  className="font-medium text-primary hover:underline"
                >
                  {t('ai.providers.undoClearKey')}
                </button>
              </span>
            ) : draft.operator_key_configured ? (
              <button
                type="button"
                onClick={() => change({ clear_operator_api_key: true, operator_api_key: '' })}
                className="inline-flex items-center gap-1 text-xs text-on-surface-variant hover:text-status-error transition-colors"
                title={t('ai.providers.clearKey')}
                aria-label={t('ai.providers.clearKey')}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{t('ai.providers.clearKey')}</span>
              </button>
            ) : null}
          </div>
          <input
            id={keyId}
            type="password"
            autoComplete="new-password"
            className="msm-input w-full"
            disabled={disabled || draft.clear_operator_api_key}
            value={draft.operator_api_key ?? ''}
            placeholder={
              draft.clear_operator_api_key
                ? t('ai.providers.keyWillBeCleared')
                : draft.operator_key_configured
                  ? t('ai.providers.keyConfigured', { hint: draft.operator_key_hint ?? '••••' })
                  : t('ai.providers.keyOptional')
            }
            aria-label={t('ai.providers.operatorKey')}
            onChange={(event) => change({ operator_api_key: event.target.value, clear_operator_api_key: false })}
          />
        </div>

        {/* Modelle & Fähigkeiten */}
        {spec?.protokoll === 'chat_completions' && (
          <div className="space-y-4 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {spec.kann_hoeren
                ? `${t('ai.providers.model')} & ${t('ai.providers.transcriptionModel')}`
                : t('ai.providers.model')}
            </h4>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {/* Chat & Denken */}
              <div className="space-y-1.5">
                <div className="flex items-end gap-2">
                  <div className="flex-1">
                    {models && models.length > 0 ? (
                      <div className="space-y-1.5">
                        <label htmlFor={modelId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                          🧠 {t('ai.providers.model')}
                        </label>
                        <Dropdown
                          id={modelId}
                          value={draft.default_model || null}
                          onChange={(default_model) => change({ default_model: default_model || '' })}
                          placeholder={t('ai.providers.modelChoose')}
                          options={[...models]
                            .sort((a, b) => Number(b.recommended) - Number(a.recommended))
                            .map((item) => ({
                              value: item.model_id,
                              label: item.model_id,
                              hint: modellHinweis(item, t),
                              icon: item.recommended
                                ? <Star className="h-3.5 w-3.5 fill-current text-primary" aria-hidden="true" />
                                : undefined,
                            }))}
                          aria-label={t('ai.providers.model')}
                        />
                      </div>
                    ) : (
                      <ProviderInput
                        label={`🧠 ${t('ai.providers.model')}`}
                        value={draft.default_model ?? ''}
                        onChange={(default_model) => change({ default_model })}
                      />
                    )}
                  </div>
                  <Button type="button" variant="ghost" disabled={!draft.provider_kind || loadingModels || fuehrtKatalog === false} onClick={() => void ladeModelle(true)}>
                    <RefreshCw className={`h-4 w-4 ${loadingModels ? 'animate-spin' : ''}`} aria-hidden="true" />
                    <span className="sr-only">{t('ai.providers.reloadModels')}</span>
                  </Button>
                </div>
                {/* Drei verschiedene Gründe für eine leere Liste, drei
                    verschiedene Sätze. „Führt keine Liste" steht zuerst, weil
                    es der einzige ist, der **kein** Zustand ist, den man
                    beheben könnte: bei Azure heisst ein Modell so, wie der
                    Betreiber sein Deployment genannt hat. Ohne diese
                    Unterscheidung meldete die Seite dort eine Störung, die
                    keine ist. */}
                {spec && !spec.fuehrt_katalog ? (
                  <p className="msm-field-help">{t('ai.providers.catalogNone')}</p>
                ) : (
                  <>
                    {models === null && !loadingModels && draft.provider_kind && (
                      <p className="msm-field-help">{t('ai.providers.catalogUnavailable')}</p>
                    )}
                    {models !== null && models.length === 0 && !loadingModels
                      && spec?.katalog_braucht_schluessel && (
                      <p className="msm-field-help">{t('ai.providers.catalogNeedsKey')}</p>
                    )}
                  </>
                )}
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

              {/* Gehör / Transkription — nur bei Anbietern, die zuhören
                  können. Ein ausfüllbares Feld, das der Sprachmodus nie liest
                  (`routers/ai_voice.py` überspringt Zugänge ohne
                  `gehoer_wege`), wäre eine Zusage ohne Deckung. */}
              {spec.kann_hoeren && (
              <div className="space-y-1.5">
                <label htmlFor={hoerenId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                  👂 {t('ai.providers.transcriptionModel')}
                </label>
                <input
                  id={hoerenId}
                  type="text"
                  className="msm-input w-full"
                  autoComplete="off"
                  spellCheck={false}
                  value={draft.transcription_model ?? ''}
                  onChange={(ereignis) => change({ transcription_model: ereignis.target.value })}
                  placeholder={draft.provider_kind === 'openai' ? 'whisper-1' : 'openai/gpt-transcribe'}
                  aria-label={t('ai.providers.transcriptionModel')}
                />
                <p className="msm-field-help">{t('ai.providers.transcriptionModelHint')}</p>
              </div>
              )}
            </div>
          </div>
        )}

        {/* Worker-Rolle: die zweite Hälfte der Provider-Zweiteilung. Das
            Gehirn antwortet mit dem Modell oben (Denkstufe wählt der Kunde im
            Chat-Kopf); die Worker arbeiten im Hintergrund mit diesem Modell
            und dieser **festen** Stufe — beides bestimmt der Betreiber, denn
            er zahlt. Leer heisst: heutiger Ein-Modell-Betrieb, kein Fehler. */}
        {spec?.protokoll === 'chat_completions' && (
          <div className="space-y-4 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                🛠️ {t('ai.providers.workerSection')}
              </h4>
              <p className="msm-field-help mt-1">{t('ai.providers.workerHint')}</p>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                {models && models.length > 0 ? (
                  <div className="space-y-1.5">
                    <label htmlFor={workerModellId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                      {t('ai.providers.workerModel')}
                    </label>
                    <Dropdown
                      id={workerModellId}
                      value={draft.worker_model || KEIN_WORKER}
                      onChange={(worker_model) => change({
                        worker_model: worker_model === KEIN_WORKER ? null : worker_model,
                        worker_reasoning_effort: null,
                      })}
                      options={[
                        { value: KEIN_WORKER, label: t('ai.providers.workerOff') },
                        ...[...models]
                          .sort((a, b) => Number(b.recommended) - Number(a.recommended))
                          .map((item) => ({
                            value: item.model_id,
                            label: item.model_id,
                            hint: modellHinweis(item, t),
                          })),
                      ]}
                      aria-label={t('ai.providers.workerModel')}
                    />
                  </div>
                ) : (
                  <ProviderInput
                    label={t('ai.providers.workerModel')}
                    value={draft.worker_model ?? ''}
                    onChange={(worker_model) => change({
                      worker_model: worker_model || null,
                      worker_reasoning_effort: null,
                    })}
                  />
                )}
                {workerModell && <ModelCapabilities model={workerModell} />}
              </div>
              {/* Die feste Denkstufe — nur wählbar, wenn der Katalog das
                  Modell kennt und es Stufen hat: die Stufen kommen immer aus
                  dem Katalog, nie aus einer Liste im Code. */}
              {workerModell?.reasoning && workerModell.efforts.length > 0 && (
                <div className="space-y-1.5">
                  <label htmlFor={workerStufeId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('ai.providers.workerEffort')}
                  </label>
                  <Dropdown
                    id={workerStufeId}
                    value={draft.worker_reasoning_effort || KEIN_WORKER}
                    onChange={(stufe) => change({
                      worker_reasoning_effort: stufe === KEIN_WORKER ? null : stufe,
                    })}
                    options={[
                      // „Nicht nachdenken" nur, wenn das Modell das zulaesst.
                      ...(workerModell.mandatory
                        ? []
                        : [{ value: KEIN_WORKER, label: t('ai.providers.workerEffortOff') }]),
                      ...workerModell.efforts.map((effort) => ({
                        value: effort,
                        label: t(`ai.reasoning.levels.${effort}`, { defaultValue: effort }),
                      })),
                    ]}
                    aria-label={t('ai.providers.workerEffort')}
                  />
                  <p className="msm-field-help">{t('ai.providers.workerEffortHint')}</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Ethics Engine: Die Reflexions- und Urteilsebene.
            Berät das Gehirn im Hintergrund vor folgenreichen Entscheidungen. */}
        {spec?.protokoll === 'chat_completions' && (
          <div className="space-y-4 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                ⚖️ {t('ai.providers.ethicsSection')}
              </h4>
              <p className="msm-field-help mt-1">{t('ai.providers.ethicsHint')}</p>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="space-y-1.5">
                {models && models.length > 0 ? (
                  <div className="space-y-1.5">
                    <label htmlFor={ethicsModellId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                      {t('ai.providers.ethicsModel')}
                    </label>
                    <Dropdown
                      id={ethicsModellId}
                      value={draft.ethics_model || KEINE_ETHICS}
                      onChange={(ethics_model) => change({
                        ethics_model: ethics_model === KEINE_ETHICS ? null : ethics_model,
                        ethics_reasoning_effort: null,
                      })}
                      options={[
                        { value: KEINE_ETHICS, label: t('ai.providers.ethicsOff') },
                        ...[...models]
                          .sort((a, b) => Number(b.recommended) - Number(a.recommended))
                          .map((item) => ({
                            value: item.model_id,
                            label: item.model_id,
                            hint: modellHinweis(item, t),
                          })),
                      ]}
                      aria-label={t('ai.providers.ethicsModel')}
                    />
                  </div>
                ) : (
                  <ProviderInput
                    label={t('ai.providers.ethicsModel')}
                    value={draft.ethics_model ?? ''}
                    onChange={(ethics_model) => change({
                      ethics_model: ethics_model || null,
                      ethics_reasoning_effort: null,
                    })}
                  />
                )}
                {ethicsModell && <ModelCapabilities model={ethicsModell} />}
              </div>

              {/* Modus / Zoning-Stufe */}
              <div className="space-y-1.5">
                <label htmlFor={ethicsModusId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                  {t('ai.providers.ethicsMode')}
                </label>
                <Dropdown
                  id={ethicsModusId}
                  value={draft.ethics_mode || 'auto'}
                  onChange={(mode) => change({ ethics_mode: mode as ProviderDraft['ethics_mode'] })}
                  options={[
                    { value: 'auto', label: t('ai.providers.ethicsModes.auto') },
                    { value: 'critical', label: t('ai.providers.ethicsModes.critical') },
                    { value: 'always', label: t('ai.providers.ethicsModes.always') },
                    { value: 'off', label: t('ai.providers.ethicsModes.off') },
                  ]}
                  aria-label={t('ai.providers.ethicsMode')}
                />
                <p className="msm-field-help">{t('ai.providers.ethicsModeHint')}</p>
              </div>

              {/* Feste Denkstufe für Ethik-Reflexion */}
              {ethicsModell?.reasoning && ethicsModell.efforts.length > 0 && (
                <div className="space-y-1.5">
                  <label htmlFor={ethicsStufeId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('ai.providers.ethicsEffort')}
                  </label>
                  <Dropdown
                    id={ethicsStufeId}
                    value={draft.ethics_reasoning_effort || KEINE_ETHICS}
                    onChange={(stufe) => change({
                      ethics_reasoning_effort: stufe === KEINE_ETHICS ? null : stufe,
                    })}
                    options={[
                      ...(ethicsModell.mandatory
                        ? []
                        : [{ value: KEINE_ETHICS, label: t('ai.providers.ethicsEffortOff') }]),
                      ...ethicsModell.efforts.map((effort) => ({
                        value: effort,
                        label: t(`ai.reasoning.levels.${effort}`, { defaultValue: effort }),
                      })),
                    ]}
                    aria-label={t('ai.providers.ethicsEffort')}
                  />
                  <p className="msm-field-help">{t('ai.providers.ethicsEffortHint')}</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Stimme bei TTS */}
        {spec?.protokoll === 'tts' && (
          <div className="space-y-4 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
            {/* Dasselbe Feld wie beim Chat-Zugang (`default_model`) — hier ist
                es das Modell, das vorliest. Der TTS-Adapter liest es am Zugang
                (leer gilt sein eigener Rueckfall); die Empfehlung kommt wie
                ueberall aus dem Katalog, nie aus der Oberflaeche. */}
            <div className="space-y-1.5">
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  {models && models.length > 0 ? (
                    <div className="space-y-1.5">
                      <label htmlFor={ttsModellId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                        🎙️ {t('ai.providers.ttsModel')}
                      </label>
                      <Dropdown
                        id={ttsModellId}
                        value={draft.default_model || null}
                        onChange={(default_model) => change({ default_model: default_model || '' })}
                        placeholder={t('ai.providers.modelChoose')}
                        options={[...models]
                          .sort((a, b) => Number(b.recommended) - Number(a.recommended))
                          .map((item) => ({
                            value: item.model_id,
                            label: item.model_id,
                            hint: modellHinweis(item, t),
                            icon: item.recommended
                              ? <Star className="h-3.5 w-3.5 fill-current text-primary" aria-hidden="true" />
                              : undefined,
                          }))}
                        aria-label={t('ai.providers.ttsModel')}
                      />
                    </div>
                  ) : (
                    <ProviderInput
                      label={`🎙️ ${t('ai.providers.ttsModel')}`}
                      value={draft.default_model ?? ''}
                      onChange={(default_model) => change({ default_model })}
                    />
                  )}
                </div>
                <Button type="button" variant="ghost" disabled={!draft.provider_kind || loadingModels || fuehrtKatalog === false} onClick={() => void ladeModelle(true)}>
                  <RefreshCw className={`h-4 w-4 ${loadingModels ? 'animate-spin' : ''}`} aria-hidden="true" />
                  <span className="sr-only">{t('ai.providers.reloadModels')}</span>
                </Button>
              </div>
              {models === null && !loadingModels && draft.provider_kind && (
                <p className="msm-field-help">{t('ai.providers.catalogUnavailable')}</p>
              )}
              {models !== null && models.length === 0 && !loadingModels
                && spec?.katalog_braucht_schluessel && (
                <p className="msm-field-help">{t('ai.providers.catalogNeedsKey')}</p>
              )}
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
            </div>
            <div className="space-y-1.5">
              <label htmlFor={stimmeId} className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                🗣️ {t('ai.providers.defaultVoice')}
              </label>
              <input
                id={stimmeId}
                type="text"
                className="msm-input w-full"
                autoComplete="off"
                spellCheck={false}
                value={draft.default_voice ?? ''}
                onChange={(ereignis) => change({ default_voice: ereignis.target.value })}
                placeholder="21m00Tcm4TlvDq8ikWAM"
                aria-label={t('ai.providers.defaultVoice')}
              />
              <p className="msm-field-help">{t('ai.providers.defaultVoiceHint')}</p>
            </div>
          </div>
        )}

        {/* Provider aktivieren Toggle */}
        <Toggle label={t('ai.providers.enabled')} checked={draft.enabled} onChange={(enabled) => change({ enabled })} />

        {/* Rückfallpreis */}
        <div className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
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
        {onDelete && <Button type="button" variant="destructive" disabled={disabled} onClick={onDelete}><Trash2 className="h-4 w-4" aria-hidden="true" />{t('common.delete')}</Button>}
        {onTest && (
          <Button type="button" variant="secondary" disabled={disabled || testing} onClick={() => void runTest()}>
            <PlugZap className="h-4 w-4" aria-hidden="true" />
            {testing ? t('common.loading') : t('ai.providers.test')}
          </Button>
        )}
        {onCancel && <Button type="button" variant="ghost" disabled={disabled} onClick={onCancel}>{t('common.cancel')}</Button>}
        <Button type="submit" disabled={disabled || !valid}><Save className="h-4 w-4" aria-hidden="true" />{saving ? t('common.loading') : t('settings.save')}</Button>
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
  return (
    <div className="flex min-h-10 items-center justify-between gap-4 text-sm text-on-surface">
      <span>{label}</span>
      <Switch checked={checked} onCheckedChange={onChange} aria-label={label} />
    </div>
  )
}
