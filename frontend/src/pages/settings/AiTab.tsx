/**
 * Rollenbasierte KI-Kontingente. Die Ansicht ist nur Konfiguration: Das
 * Backend löst mehrere Rollen auf und erzwingt die Werte an den AI-Endpunkten.
 *
 * Es wird bewusst immer nur *eine* Rolle gleichzeitig gezeigt. Vorher standen
 * alle Rollen mit je sechs Zahlenfeldern untereinander — bei einer Handvoll
 * Rollen war die Seite nicht mehr überschaubar und man verlor beim Scrollen,
 * welches Feld zu welcher Rolle gehört.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BarChart3, Bot, KeyRound, Save, Sliders, Sparkles } from 'lucide-react'

import { api, SanitizedApiError } from '@/api/client'
import { AiMemoryManager } from '@/components/ai/AiMemoryManager'
import { AiSkillManager } from '@/components/ai/AiSkillManager'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Dropdown, NumberStepper, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { AiContextSettings } from './AiContextSettings'
import { AiCostSettings } from './AiCostSettings'
import { AiLearningSettings } from './AiLearningSettings'
import { AiProvidersSettings } from './AiProvidersSettings'
import { AiUsageSettings } from './AiUsageSettings'
import { AiWebSearchSettings } from './AiWebSearchSettings'

export type AiSubTab = 'providers' | 'features' | 'limits' | 'usage'

const AI_TABS: TabDef<AiSubTab>[] = [
  { id: 'providers', labelKey: 'aiSettings.subtabs.providers', icon: KeyRound },
  { id: 'features', labelKey: 'aiSettings.subtabs.features', icon: Sparkles },
  { id: 'limits', labelKey: 'aiSettings.subtabs.limits', icon: Sliders },
  { id: 'usage', labelKey: 'aiSettings.subtabs.usage', icon: BarChart3 },
]

export interface AiRoleLimits {
  role_id: number
  role_name: string
  /** False heisst: für diese Rolle ist nichts gespeichert (alle Werte null). */
  configured: boolean
  daily_token_limit: number | null
  weekly_token_limit: number | null
  monthly_token_limit: number | null
  requests_per_minute: number | null
  concurrent_operations: number | null
  monthly_cost_limit_cents: number | null
  /**
   * Wieviele Memory-Eintraege in **je einem Bereich** liegen duerfen — hier
   * stand vorher „was ein Benutzer dieser Rolle anlegen darf", und das war
   * falsch. Gezaehlt wird je scope_identity: persoenlich allgemein, je Server
   * und je Team. „Je Server" meint dabei die persoenlichen Notizen zu einem
   * Server; das geteilte „Wissen dieses Servers" ist ein anderer Bereich und
   * haengt wie das panelweite Gedaechtnis an gar keiner Rolle. Wieviele
   * Bereiche es gibt, bestimmt der Benutzer selbst; wer 20 Server sieht, hat
   * bei „25" nicht 25 Eintraege, sondern 25 persoenliche plus 25 je Server
   * plus 25 je selbst gegruendetem Team.
   *
   * `null` heisst hier — anders als bei den Kontingenten darueber — *nicht*
   * unbegrenzt: das Backend faellt auf MAX_SYSTEM_SCOPE_ENTRIES zurueck,
   * sobald die Rollenaufloesung keine Zahl liefert (`resolve_scope_memory_limit`
   * in services/ai_limit_service.py). Die Zahl stand hier ausgeschrieben und
   * war damit eine zweite, ungebundene Fassung derselben Konstante — die
   * Waechter im Backend halten die Locale-Texte und den Feld-Deckel an ihr
   * fest, dieser Docblock lag ausserhalb dessen, was sie lesen, und haette die
   * alte Zahl ueberlebt. Ohne diesen Rueckfall waere die Grenze auf jeder
   * Bestandsanlage ersatzlos weggefallen, denn nach der Migration traegt jede
   * Rolle NULL — und der Leseweg hat keinen Deckel: jeder sichtbare Eintrag
   * wird bei jeder Chatanfrage einzeln entschluesselt.
   *
   * Der Rueckfall greift benutzerweit, nicht je Rollenkarte: ein leeres Feld
   * traegt in der Rollenaufloesung nichts mehr bei (FELDER_OHNE_UNBEGRENZT),
   * damit eine zusaetzliche Rolle den Vorrat nie senkt. Die Systemgrenze gilt
   * also erst, wenn *keine* Rolle des Benutzers eine Zahl traegt — was diese
   * Karte zeigt, ist nur eine der Rollen, die er tragen kann.
   *
   * Team-Gedaechtnis zaehlt gegen das Limit des Team-Gruenders, nicht gegen das
   * des schreibenden Mitglieds. Das ist an der Oberflaeche sonst nicht zu
   * erraten: wer hier eine Rolle knapp haelt, begrenzt damit auch die Teams,
   * die ein Traeger dieser Rolle gegruendet hat.
   */
  max_memory_entries: number | null
  /**
   * Hoechste erlaubte Denktiefe als Rang: 0 = gar nicht, 1 = minimal … 6 = max.
   * `null` heisst unbegrenzt — dieselbe Bedeutung wie bei den Kontingenten.
   *
   * Ein Rang und kein Wort, weil jedes Modell andere Stufen kennt: gemessen
   * gibt es bei OpenRouter 20 verschiedene Stufenlisten. Gewaehlt wird spaeter
   * aus den echten Stufen des Modells, der Rang vergleicht nur.
   */
  max_reasoning_effort: number | null
  updated_at: string | null
}

/**
 * Die Woerter zu den Raengen — dieselbe Reihenfolge wie
 * `services/ai_reasoning.RANGFOLGE` im Backend. Rang 0 ist „gar nicht" und hat
 * dort kein Wort; hier bekommt es eines, weil es in der Auswahl stehen muss.
 */
const REASONING_RANKS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const

type LimitField = Exclude<
  keyof AiRoleLimits,
  'role_id' | 'role_name' | 'configured' | 'updated_at'
>

const FIELD_DEFINITIONS: Array<{
  key: LimitField
  labelKey: string
  max: number
  step: number
  /**
   * Statt eines Zahlenfelds eine Auswahl mit Woertern. Nur fuer die Denktiefe:
   * „4" sagt niemandem etwas, „hoch" schon. Alle uebrigen Felder sind echte
   * Mengen und bleiben Zahlen.
   */
  ranks?: readonly string[]
  /**
   * Ein ruhiger Satz unter dem Feld, fuer die Faelle, in denen Beschriftung und
   * Schalter allein in die Irre fuehren. Bisher hat das kein Feld gebraucht;
   * `max_memory_entries` braucht es, weil dort viererlei nicht selbsterklaerend
   * ist: die Zahl gilt je Bereich, „Unbegrenzt" bedeutet die Systemgrenze, im
   * Teambereich entscheidet die Rolle des Gruenders statt der des Schreibenden,
   * und Anlagen- wie Panelwissen haengen an gar keiner Rolle.
   *
   * Zweimal hat dieser Satz schon gelogen, und beide Male, weil er eine
   * Rollenkarte beschrieb, wo das Backend benutzerweit aufloest. Erst zaehlte
   * er den Teambereich auf, als sei er von der eingestellten Zahl gedeckelt —
   * wer „basic" auf 5 setzt, deckelt damit aber kein einziges Team, dessen
   * Gruender eine andere Rolle traegt; ein Basic-Kunde schreibt im Team eines
   * VIP-Gruenders weiterhin dessen 500. Dann versprach er die Systemgrenze
   * „ohne eigene Zahl": das stimmte, solange ein leeres Feld jede Zahl
   * verdraengte, und wurde falsch, als es nichts mehr beitrug. Wer heute an der
   * knappen Rolle plant, plant fuer Benutzer, die daneben eine grosszuegige
   * tragen — und die gewinnt.
   *
   * Optional, damit die uebrigen Felder unveraendert bleiben — sie sagen genau
   * das, was ihre Beschriftung sagt.
   */
  hintKey?: string
}> = [
  // Muss `TOKEN_LIMIT_MAX` im Backend entsprechen: die Tokenspalten sind
  // PostgreSQL INTEGER, höhere Werte würden beim Speichern abbrechen.
  { key: 'daily_token_limit', labelKey: 'aiSettings.dailyTokens', max: 2_147_483_647, step: 1_000 },
  { key: 'weekly_token_limit', labelKey: 'aiSettings.weeklyTokens', max: 2_147_483_647, step: 10_000 },
  { key: 'monthly_token_limit', labelKey: 'aiSettings.monthlyTokens', max: 2_147_483_647, step: 10_000 },
  { key: 'requests_per_minute', labelKey: 'aiSettings.requestsPerMinute', max: 10_000, step: 1 },
  { key: 'concurrent_operations', labelKey: 'aiSettings.concurrentOperations', max: 100, step: 1 },
  { key: 'monthly_cost_limit_cents', labelKey: 'aiSettings.monthlyCostCents', max: 1_000_000_000, step: 100 },
  // 1_000 muss `MAX_MEMORY_ENTRIES_MAX` im Backend entsprechen: dort ist der
  // Deckel bewusst niedrig, weil jeder Eintrag beim Promptaufbau einzeln
  // entschlüsselt wird. Ein hier großzügigeres Maximum ließe den Betreiber
  // Werte eintragen, die das Backend abweist.
  // „Muss entsprechen" war bis eben eine blosse Bitte: die Zahl steht hier,
  // die Konstante dort, und keine Prüfung sah beide. `test_ai_role_limits.py`
  // liest diese Zeile jetzt und vergleicht sie — wer den Backend-Deckel
  // verschiebt, bekommt dort einen roten Test statt hier ein stilles Formular,
  // das gültige Werte abweist oder ungültige durchlässt.
  {
    key: 'max_memory_entries',
    labelKey: 'aiSettings.maxMemoryEntries',
    max: 1_000,
    step: 10,
    hintKey: 'aiSettings.maxMemoryEntriesHint',
  },
  {
    key: 'max_reasoning_effort',
    labelKey: 'aiSettings.maxReasoningEffort',
    max: REASONING_RANKS.length - 1,
    step: 1,
    ranks: REASONING_RANKS,
  },
]

/** Wandelt Stepper-Text nur dann um, wenn er eine sichere Ganzzahl darstellt. */
function parseLimitValue(raw: string, max: number): number | null {
  if (!/^\d+$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value >= 0 && value <= max ? value : null
}

export function AiTab() {
  const { t } = useTranslation()
  const canRead = useHasPermission('panel.settings.read')
  const canWrite = useHasPermission('panel.settings.write')
  const canManageSkills = useHasPermission('ai.skills.manage')
  // Eigenes Recht, nicht `panel.settings.read`: wer Verbraeuche sieht, sieht
  // das Nutzungsverhalten fremder Benutzer.
  const canReadUsage = useHasPermission('ai.usage.read.all')
  const [activeTab, setActiveTab] = useState<AiSubTab>('providers')
  const [rows, setRows] = useState<AiRoleLimits[]>([])
  const [selectedRoleId, setSelectedRoleId] = useState<number | null>(null)
  const [loading, setLoading] = useState(canRead)
  const [saving, setSaving] = useState(false)

  const visibleTabs = useMemo(() => {
    return AI_TABS.filter((tab) => tab.id !== 'usage' || canReadUsage)
  }, [canReadUsage])

  useEffect(() => {
    if (!visibleTabs.some((tab) => tab.id === activeTab)) {
      setActiveTab('providers')
    }
  }, [visibleTabs, activeTab])

  useEffect(() => {
    if (!canRead) {
      setLoading(false)
      return
    }
    let active = true
    api<AiRoleLimits[]>('/ai/settings/role-limits')
      .then((data) => {
        if (!active) return
        const list = Array.isArray(data) ? data : []
        setRows(list)
        // Bevorzugt die erste bereits konfigurierte Rolle: dort gibt es etwas
        // zu sehen. Ist nichts konfiguriert, greift schlicht die erste Rolle.
        setSelectedRoleId((list.find((row) => row.configured) ?? list[0])?.role_id ?? null)
      })
      .catch((error: unknown) => {
        // Vorzeigbar ist nur, was aus einer verarbeiteten Backend-Antwort kommt
        // (siehe api/client.ts). Ist das Panel nicht erreichbar, wirft `fetch`
        // einen blanken TypeError — dessen `message` ist die englische Meldung
        // des Browsers („Failed to fetch") und stünde unübersetzt in der
        // Oberfläche, egal welche Sprache eingestellt ist.
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('aiSettings.loadFailed'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [canRead, t])

  const selected = useMemo(
    () => rows.find((row) => row.role_id === selectedRoleId) ?? null,
    [rows, selectedRoleId],
  )

  /** Ändert genau ein Feld lokal; gespeichert wird anschließend das Vollset. */
  const updateField = (roleId: number, field: LimitField, value: number | null) => {
    setRows((current) => current.map((row) => (
      row.role_id === roleId ? { ...row, [field]: value } : row
    )))
  }

  const save = async (row: AiRoleLimits) => {
    if (!canWrite || saving) return
    setSaving(true)
    try {
      const payload = Object.fromEntries(
        FIELD_DEFINITIONS.map(({ key }) => [key, row[key]]),
      )
      const updated = await api<AiRoleLimits>(`/ai/settings/role-limits/${row.role_id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      setRows((current) => current.map((item) => (
        item.role_id === updated.role_id ? updated : item
      )))
      toast.success(t('aiSettings.saved'))
    } catch (error: unknown) {
      // Wie beim Laden: ein roher Laufzeitfehler bekommt den übersetzten Satz.
      toast.error(error instanceof SanitizedApiError ? error.message : t('aiSettings.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  if (!canRead) {
    return <div className="msm-card p-6 text-sm text-on-surface-variant">{t('aiSettings.noPermission')}</div>
  }
  if (loading) {
    return <div className="flex h-64 items-center justify-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
  }

  return (
    <div className="space-y-6">
      <TabBar
        tabs={visibleTabs}
        active={activeTab}
        onChange={setActiveTab}
        ariaLabel={t('aiSettings.tabsAriaLabel')}
      />

      {activeTab === 'providers' && (
        <div className="space-y-6">
          <AiProvidersSettings canWrite={canWrite} />
          {/* Direkt unter der Providerwahl: wie groß der Kontext ist, entscheidet
              das dort gewählte Modell — einzustellen bleibt nur, wie voll er werden
              darf, bevor zusammengefasst wird. */}
          <AiContextSettings canWrite={canWrite} />
          {/* Die Währung steht direkt hinter der Providerwahl, weil der Preis dort
              eingetragen wird: welche Zahl „1,20" bedeutet, entscheidet sich hier. */}
          <AiCostSettings canWrite={canWrite} />
        </div>
      )}

      {activeTab === 'features' && (
        <div className="space-y-6">
          <AiWebSearchSettings canWrite={canWrite} />
          <AiLearningSettings canWrite={canWrite} />

          {/* Panelweite Skills gehören zum Betreiber, nicht ins Profil eines
              Benutzers — und damit neben die Freigabe der KI-gelernten oben.
              Bis eben wurden sie über dasselbe Panel angelegt, das im Profil
              stand; wer dort etwas eintrug, schrieb unbemerkt für alle. */}
          {canManageSkills && <AiSkillManager scope={{ kind: 'panel', canManage: canWrite }} />}

          {/* Panelweites Gedaechtnis gilt fuer **jeden** Benutzer und lief bisher in
              jedem Gespraech mit, ohne dass es irgendwo sichtbar war — erreichbar
              nur ueber die API. Was fuer alle gilt, gehoert dorthin, wo der
              Betreiber es sieht. */}
          <AiMemoryManager scope={{ kind: 'panel', canManage: canWrite }} />
        </div>
      )}

      {activeTab === 'limits' && (
        <div className="space-y-6">
          <div className="msm-card p-6">
            <div className="mb-3 flex items-center gap-2">
              <Bot className="h-5 w-5 text-primary" aria-hidden="true" />
              <h3 className="font-headline text-lg font-semibold text-on-surface">{t('aiSettings.title')}</h3>
            </div>
            <p className="max-w-3xl text-sm text-on-surface-variant">{t('aiSettings.description')}</p>
            {/* Der Regeltext steht ueber dem Feldraster und wird zuerst gelesen —
                er muss deshalb selbst sagen, wo er nicht gilt. Bis eben drehte er
                die Sonderregel des Memory-Feldes genau um („eine explizit
                unbegrenzte Rolle gewinnt ueber begrenzte Rollen"), waehrend der
                Feldhinweis eine Zeile tiefer das Gegenteil sagte. Wer oben las,
                legte an der grosszuegigen Rolle „Unbegrenzt" um — und nahm ihr
                damit jeden Beitrag, statt ihr einen zu geben. */}
            <p className="mt-2 max-w-3xl text-xs text-on-surface-variant">{t('aiSettings.ruleHelp')}</p>
          </div>

          {rows.length === 0 && (
            <div className="msm-card p-6 text-sm text-on-surface-variant">{t('aiSettings.noRoles')}</div>
          )}

          {selected && (
            <section className="msm-card p-6" aria-labelledby="ai-role-limits-title">
              <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
                <label className="block w-full max-w-sm space-y-1.5">
                  <span id="ai-role-limits-title" className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('aiSettings.selectRole')}
                  </span>
                  <Dropdown
                    value={String(selected.role_id)}
                    onChange={(value) => setSelectedRoleId(Number(value))}
                    options={rows.map((row) => ({
                      value: String(row.role_id),
                      label: row.role_name,
                      hint: row.configured ? t('aiSettings.configured') : t('aiSettings.notConfigured'),
                    }))}
                    disabled={saving}
                    aria-label={t('aiSettings.selectRole')}
                  />
                </label>
                {canWrite && (
                  <Button
                    type="button"
                    disabled={saving}
                    onClick={() => void save(selected)}
                    aria-label={`${t('settings.save')}: ${selected.role_name}`}
                  >
                    <Save className="h-4 w-4" aria-hidden="true" />
                    {saving ? t('common.loading') : t('settings.save')}
                  </Button>
                )}
              </div>

              {/* Derselbe Grund wie beim Regeltext darueber, nur naeher am Schaden:
                  dieser Kasten erscheint genau an einer Rolle, deren Memory-Feld
                  leer ist. „Solange keine Rolle konfiguriert ist, gilt unbegrenzt"
                  stimmt fuer die Kontingente und nicht fuer den Vorrat — wer daraus
                  schloss, ein leeres Feld lasse das Gedaechtnis offen, plante gegen
                  eine Zahl, die das Panel nie durchsetzt. */}
              {!selected.configured && (
                <p className="mb-5 rounded-lg border border-outline-variant/40 bg-surface-container-low/45 p-3 text-xs leading-5 text-on-surface-variant">
                  {t('aiSettings.notConfiguredHint')}
                </p>
              )}

              <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
                {FIELD_DEFINITIONS.map(({ key, labelKey, max, step, ranks, hintKey }) => {
                  const unlimited = selected[key] === null
                  const label = t(labelKey)
                  const fieldId = `ai-${selected.role_id}-${key}`
                  const hintId = hintKey ? `${fieldId}-hint` : undefined
                  return (
                    <div key={key} className="space-y-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
                      <label htmlFor={fieldId} className="block min-h-10 text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                        {label}
                      </label>
                      {ranks ? (
                        <Dropdown
                          id={fieldId}
                          value={String(selected[key] ?? 0)}
                          disabled={!canWrite || unlimited || saving}
                          onChange={(wert) => updateField(selected.role_id, key, Number(wert))}
                          options={ranks.map((rank, rang) => ({
                            value: String(rang),
                            label: rang === 0
                              ? t('ai.reasoning.off')
                              : t(`ai.reasoning.levels.${rank}`, { defaultValue: rank }),
                          }))}
                          aria-label={`${label}: ${selected.role_name}`}
                          aria-describedby={hintId}
                        />
                      ) : (
                        <NumberStepper
                          id={fieldId}
                          min={0}
                          max={max}
                          step={step}
                          value={selected[key] ?? 0}
                          disabled={!canWrite || unlimited || saving}
                          onValueChange={(raw) => {
                            const parsed = parseLimitValue(raw, max)
                            if (parsed !== null) updateField(selected.role_id, key, parsed)
                          }}
                          aria-label={`${label}: ${selected.role_name}`}
                          aria-describedby={hintId}
                        />
                      )}
                      <div className="flex min-h-10 items-center justify-between gap-3">
                        <span className="text-xs text-on-surface-variant">{t('aiSettings.unlimited')}</span>
                        <Switch
                          checked={unlimited}
                          disabled={!canWrite || saving}
                          onCheckedChange={(next) => updateField(selected.role_id, key, next ? null : 0)}
                          aria-label={`${t('aiSettings.unlimited')}: ${label}: ${selected.role_name}`}
                          aria-describedby={hintId}
                        />
                      </div>
                      {hintKey && (
                        <p id={hintId} className="text-xs leading-5 text-on-surface-variant">
                          {t(hintKey)}
                        </p>
                      )}
                    </div>
                  )
                })}
              </div>
            </section>
          )}
        </div>
      )}

      {activeTab === 'usage' && canReadUsage && (
        <div className="space-y-6">
          <AiUsageSettings />
        </div>
      )}
    </div>
  )
}
