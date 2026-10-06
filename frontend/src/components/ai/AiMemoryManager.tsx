import {
  Archive,
  BookOpen,
  Brain,
  BrainCircuit,
  Calendar,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  Download,
  Flame,
  History,
  Pencil,
  Plus,
  RotateCcw,
  Save,
  ShieldAlert,
  Tag,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  aiApi,
  type AiMemoryAnsicht,
  type AiMemoryEntry,
  type AiMemoryFassung,
  type AiMemoryPage,
  type AiMemoryThema,
} from '@/api/ai'
import { api, SanitizedApiError } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Dropdown,
  Input,
  Kurzinfo,
  Pagination,
  Switch,
  TabBar,
  Textarea,
  Versionsliste,
  type TabDef,
} from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

import { AiKnowledgeShell } from './AiKnowledgeShell'
import { AiMemoryImportModal } from './AiMemoryImportModal'
import {
  type AiKnowledgeScope,
  memoryScopeName,
  scopeCanManage,
  scopeServerId,
  scopeTeamId,
} from './knowledgeScope'
import { formatMemoryDate, formatMemoryKey } from './memoryFormatters'

type Herkunft = 'all' | 'user' | 'ai'

interface ServerOption {
  id: number
  name: string
}

interface Props {
  /** Ohne Angabe: das eigene Gedächtnis. */
  scope?: AiKnowledgeScope
}

const CHUNK_SIZE = 30

/** So lange lässt sich zurückholen, was Singra vergessen hat (`VERGESSEN_TAGE`). */
const VERGESSEN_TAGE = 30

const ANSICHTEN: TabDef<AiMemoryAnsicht>[] = [
  { id: 'aktiv', labelKey: 'ai.memory.ansichten.aktiv', icon: Brain },
  { id: 'vergessen', labelKey: 'ai.memory.ansichten.vergessen', icon: Archive },
]

/**
 * Gleichnamige Themen werden ein Filter.
 *
 * Im Profil stehen zwei Bereiche in einer Liste (allgemein und zu einzelnen
 * Servern), und jeder hat seine eigenen Themen. „Familie“ allgemein und
 * „familie“ zu Server 62 sind für den Menschen dasselbe Wort.
 */
interface Themengruppe {
  schluessel: string
  name: string
  ids: string[]
  anzahl: number
}

function themenGruppieren(themen: AiMemoryThema[]): Themengruppe[] {
  const gruppen = new Map<string, Themengruppe>()
  for (const thema of themen) {
    const schluessel = thema.name.toLocaleLowerCase()
    const gruppe = gruppen.get(schluessel)
    if (gruppe) {
      gruppe.ids.push(thema.id)
      gruppe.anzahl += thema.anzahl
    } else {
      gruppen.set(schluessel, { schluessel, name: thema.name, ids: [thema.id], anzahl: thema.anzahl })
    }
  }
  return [...gruppen.values()]
}

function kuerzen(text: string, laenge = 60): string {
  const flach = text.replace(/\s+/g, ' ').trim()
  return flach.length > laenge ? `${flach.slice(0, laenge).trimEnd()} …` : flach
}

/** Titel, sonst der lesbar gemachte Name aus dem Altbestand — oder nichts. */
function ueberschrift(entry: AiMemoryEntry): string | null {
  if (entry.titel) return entry.titel
  return entry.key ? formatMemoryKey(entry.key) : null
}

/** Wie eine Erinnerung in Rückfragen und Knopfnamen heißt. */
function benennung(entry: AiMemoryEntry): string {
  return ueberschrift(entry) ?? kuerzen(entry.value)
}

interface Ladeziel {
  seite?: number
  groesse?: number
  ansicht?: AiMemoryAnsicht
  themaIds?: string[]
}

/**
 * Erinnerungen einsehen und pflegen im kompakten Logbuch-Stil.
 *
 * Seit Gedächtnis v2 ist eine Erinnerung ein bis fünf Sätze ohne Namen, mit
 * Titel und Thema daneben. Jede Änderung legt den Stand davor als Fassung ab
 * („Verlauf“), und was Singra vergisst, steht 30 Tage unter „Vergessen“, bevor
 * es endgültig weg ist. Altbestand behält seinen Namen, bis er umgeschrieben
 * ist.
 */
export function AiMemoryManager({ scope = { kind: 'user' } }: Props) {
  const { t, i18n } = useTranslation()
  const allowed = useHasPermission('ai.memory.use')
  const darfAendern = scopeCanManage(scope)
  const teamId = scopeTeamId(scope)
  const serverId = scopeServerId(scope)

  const [entries, setEntries] = useState<AiMemoryEntry[]>([])
  const [seite, setSeite] = useState(1)
  const [gesamt, setGesamt] = useState(0)
  const [loeschbar, setLoeschbar] = useState(0)
  const [seitengroesse, setSeitengroesse] = useState(0)
  const [ansicht, setAnsicht] = useState<AiMemoryAnsicht>('aktiv')
  const [themen, setThemen] = useState<AiMemoryThema[]>([])
  const [themaWahl, setThemaWahl] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [suche, setSuche] = useState('')
  const [herkunft, setHerkunft] = useState<Herkunft>('all')
  const [text, setText] = useState('')
  const [titel, setTitel] = useState('')
  const [thema, setThema] = useState('')
  const [bearbeitet, setBearbeitet] = useState<AiMemoryEntry | null>(null)
  const [serverNamen, setServerNamen] = useState<Map<number, string>>(new Map())
  const [busy, setBusy] = useState(false)
  const [importOffen, setImportOffen] = useState(false)
  const [verlauf, setVerlauf] = useState<{ entry: AiMemoryEntry; fassungen: AiMemoryFassung[] } | null>(null)
  const [verlaufHolt, setVerlaufHolt] = useState<string | null>(null)

  // Accordion-Zustand: Standardmäßig alle eingeklappt
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  // Lazy Loading / Progressiver Nachlademechanismus für flüssiges Scrollen
  const [renderedLimit, setRenderedLimit] = useState(CHUNK_SIZE)
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const formRef = useRef<HTMLFormElement | null>(null)
  const textRef = useRef<HTMLTextAreaElement | null>(null)
  // Jede Ladung bekommt eine Nummer, übernommen wird nur die jüngste. Sonst
  // konnte eine langsame Antwort für „Aktuell“ nach dem Wechsel auf
  // „Vergessen“ eintreffen und die falsche Liste unter den Reiter legen.
  const ladeNummer = useRef(0)

  const themenGruppen = useMemo(() => themenGruppieren(themen), [themen])
  const gewaehlteIds = themenGruppen.find((gruppe) => gruppe.schluessel === themaWahl)?.ids ?? []

  const holen = (offset: number, zielAnsicht: AiMemoryAnsicht, themaIds: string[]): Promise<AiMemoryPage> => {
    const filter = { status: zielAnsicht, thema: themaIds }
    return scope.kind === 'user'
      ? aiApi.listPersonalMemory(offset, filter)
      : aiApi.listScopeMemory(memoryScopeName(scope), serverId, teamId, offset, filter)
  }

  const themenHolen = (): Promise<AiMemoryThema[]> => (
    scope.kind === 'user'
      ? aiApi.listPersonalTopics()
      : aiApi.listScopeTopics(memoryScopeName(scope), serverId, teamId)
  )

  const uebernehmen = (ladung: AiMemoryPage, zielSeite: number) => {
    setEntries(ladung.entries)
    setGesamt(ladung.total)
    setLoeschbar(ladung.clearable)
    setSeitengroesse(ladung.limit)
    setSeite(zielSeite)
  }

  const laden = async ({
    seite: zielSeite = seite,
    groesse = seitengroesse,
    ansicht: zielAnsicht = ansicht,
    themaIds = gewaehlteIds,
  }: Ladeziel = {}): Promise<AiMemoryPage> => {
    const nummer = ++ladeNummer.current
    const ladung = await holen(Math.max(0, zielSeite - 1) * groesse, zielAnsicht, themaIds)
    if (nummer !== ladeNummer.current) return ladung
    const letzte = Math.max(1, Math.ceil(ladung.total / ladung.limit))
    if (zielSeite > letzte) {
      return laden({ seite: letzte, groesse: ladung.limit, ansicht: zielAnsicht, themaIds })
    }
    uebernehmen(ladung, zielSeite)
    return ladung
  }

  /**
   * Nach jeder Änderung: die Themen neu zählen und die Seite neu holen. Steht
   * unter dem gewählten Thema nichts mehr, fällt der Filter weg, statt eine
   * leere Liste zu zeigen, die wie ein leeres Gedächtnis aussieht.
   */
  const nachAenderung = async (zielSeite = seite): Promise<AiMemoryPage> => {
    const neue = await themenHolen().catch(() => themen)
    setThemen(neue)
    const gruppe = themenGruppieren(neue).find((eintrag) => eintrag.schluessel === themaWahl)
    if (!gruppe && themaWahl) setThemaWahl('')
    return laden({ seite: zielSeite, themaIds: gruppe?.ids ?? [] })
  }

  const formLeeren = () => {
    setText(''); setTitel(''); setThema(''); setBearbeitet(null)
  }

  useEffect(() => {
    if (!allowed) return
    let active = true
    setSuche(''); setHerkunft('all'); setAnsicht('aktiv'); setThemaWahl('')
    setText(''); setTitel(''); setThema(''); setBearbeitet(null)
    setExpandedIds(new Set())
    const nummer = ++ladeNummer.current
    Promise.all([
      holen(0, 'aktiv', []),
      aiApi.getMemoryPreference(),
      scope.kind === 'user'
        ? api<ServerOption[]>('/servers').catch(() => [] as ServerOption[])
        : Promise.resolve([] as ServerOption[]),
      themenHolen().catch(() => [] as AiMemoryThema[]),
    ])
      .then(([ladung, preference, servers, geladeneThemen]) => {
        if (!active) return
        if (nummer === ladeNummer.current) uebernehmen(ladung, 1)
        setEnabled(preference.enabled)
        setServerNamen(new Map(servers.map((row) => [row.id, row.name])))
        setThemen(geladeneThemen)
      })
      .catch(() => { if (active) toast.error(t('ai.memory.errors.load')) })
    return () => { active = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, scope.kind, teamId, serverId, t])

  // Die Reihenfolge ist die des Servers: an ihr ist die Seite geschnitten
  // (`_SEITENORDNUNG`, im Vergessenen das Vergessensdatum). Wer hier anders
  // sortierte, zeigte eine Seite in einer anderen Ordnung, als sie entstand.
  const sichtbar = useMemo(() => {
    const nadel = suche.trim().toLowerCase()
    return entries
      .filter((entry) => herkunft === 'all' || entry.origin === herkunft)
      .filter((entry) => !nadel
        || entry.value.toLowerCase().includes(nadel)
        || (entry.titel ?? '').toLowerCase().includes(nadel)
        || (entry.thema?.name ?? '').toLowerCase().includes(nadel)
        || (entry.key ?? '').toLowerCase().includes(nadel)
        || formatMemoryKey(entry.key ?? '').toLowerCase().includes(nadel))
  }, [entries, herkunft, suche])

  // Zurücksetzen des Lazy-Loading-Limits beim Filtern
  useEffect(() => {
    setRenderedLimit(CHUNK_SIZE)
  }, [suche, herkunft, seite, ansicht, themaWahl])

  // Progressive Anzeige limitieren
  const displayEntries = useMemo(() => {
    return sichtbar.slice(0, renderedLimit)
  }, [sichtbar, renderedLimit])

  // Automatische IntersectionObserver-Anbindung für Lazy Loading
  useEffect(() => {
    if (displayEntries.length >= sichtbar.length) return
    const sentinel = sentinelRef.current
    if (!sentinel || typeof IntersectionObserver === 'undefined') return

    const observer = new IntersectionObserver((entriesList) => {
      if (entriesList[0]?.isIntersecting) {
        setRenderedLimit((prev) => Math.min(prev + CHUNK_SIZE, sichtbar.length))
      }
    }, { rootMargin: '200px' })

    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [displayEntries.length, sichtbar.length])

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const allExpanded = sichtbar.length > 0 && sichtbar.every((e) => expandedIds.has(e.id))

  const toggleAllExpanded = () => {
    if (allExpanded) {
      setExpandedIds(new Set())
    } else {
      setExpandedIds(new Set(sichtbar.map((e) => e.id)))
    }
  }

  const imAktuellen = ansicht === 'aktiv'
  const werkzeugleiste = entries.length > 3 || suche !== '' || herkunft !== 'all'
  const seitenzahl = seitengroesse > 0 ? Math.max(1, Math.ceil(gesamt / seitengroesse)) : 1

  if (!allowed) return null

  const fehlermeldung = (error: unknown, ersatz: string) => (
    error instanceof SanitizedApiError ? error.message : t(ersatz)
  )

  const blaettern = (naechste: number) => {
    setBusy(true)
    void laden({ seite: naechste })
      .catch(() => toast.error(t('ai.memory.errors.load')))
      .finally(() => setBusy(false))
  }

  const ansichtWechseln = (naechste: AiMemoryAnsicht) => {
    // Nicht mitten in einer Änderung: deren Nachladen gehört zur Ansicht, in
    // der sie begonnen hat.
    if (naechste === ansicht || busy) return
    setAnsicht(naechste)
    setSuche(''); setHerkunft('all'); setThemaWahl('')
    formLeeren()
    setExpandedIds(new Set())
    setBusy(true)
    void laden({ seite: 1, ansicht: naechste, themaIds: [] })
      .catch(() => toast.error(t('ai.memory.errors.load')))
      .finally(() => setBusy(false))
  }

  const themaWaehlen = (schluessel: string) => {
    setThemaWahl(schluessel)
    setBusy(true)
    void laden({ seite: 1, themaIds: themenGruppen.find((g) => g.schluessel === schluessel)?.ids ?? [] })
      .catch(() => toast.error(t('ai.memory.errors.load')))
      .finally(() => setBusy(false))
  }

  const speichern = async (event: React.FormEvent) => {
    event.preventDefault()
    const satz = text.trim()
    if (!satz || busy) return
    setBusy(true)
    const titelWert = titel.trim() || null
    const themaWert = thema.trim() || null
    try {
      if (bearbeitet !== null) {
        // Über die Kennung, nicht über den Bereich: eine Servernotiz bleibt
        // dabei, wo sie ist, statt als persönliche Kopie neu zu entstehen.
        await aiApi.updateMemory(bearbeitet.id, {
          text: satz, titel: titelWert, thema: themaWert, fassung: bearbeitet.fassung,
        })
      } else {
        await aiApi.createMemory({
          ...(scope.kind === 'team'
            ? { scope: 'team', team_id: scope.teamId }
            : scope.kind === 'server_shared'
              ? { scope: 'server_shared', server_id: scope.serverId }
              : { scope: scope.kind }),
          text: satz, titel: titelWert, thema: themaWert,
        })
      }
      formLeeren()
      await nachAenderung()
      toast.success(t('ai.memory.saved'))
    } catch (error: unknown) {
      toast.error(fehlermeldung(error, 'ai.memory.errors.save'))
      // Jemand anderes war schneller (ein zweites Fenster, die Pflege im
      // Hintergrund). Der eigene Text bleibt im Formular; die Liste zeigt den
      // Stand, gegen den er jetzt gespeichert würde.
      //
      // Geholt wird die eine Zeile und nicht nur die Seite: die Ordnung folgt
      // dem letzten Gebrauch, und eine Erinnerung, die inzwischen auf einer
      // anderen Seite steht, hielt sonst ihre alte Fassung — jedes weitere
      // Speichern wäre wieder 409 gewesen.
      if (bearbeitet !== null && error instanceof SanitizedApiError && error.status === 409) {
        const [neu] = await Promise.all([
          aiApi.getMemory(bearbeitet.id).catch(() => null),
          laden().catch(() => null),
        ])
        if (neu && neu.status === 'aktiv') {
          setBearbeitet(neu)
          setExpandedIds((prev) => new Set(prev).add(neu.id))
        } else {
          // Vergessen oder gelöscht: es gibt keinen Stand mehr, gegen den
          // gespeichert würde. Der Text bleibt und wird neu angelegt.
          setBearbeitet(null)
        }
      }
    } finally { setBusy(false) }
  }

  const entfernen = async (entry: AiMemoryEntry) => {
    if (!await confirm({
      message: t(imAktuellen ? 'ai.memory.deleteConfirm' : 'ai.memory.deleteForeverConfirm', {
        name: benennung(entry),
      }),
      confirmText: t('common.delete'), danger: true,
    })) return
    setBusy(true)
    try {
      await aiApi.deleteMemory(entry.id)
      if (bearbeitet?.id === entry.id) formLeeren()
      await nachAenderung()
    } catch { toast.error(t('ai.memory.errors.delete')) } finally { setBusy(false) }
  }

  const zurueckholen = async (entry: AiMemoryEntry) => {
    setBusy(true)
    try {
      await aiApi.restoreMemory(entry.id)
      await nachAenderung()
      toast.success(t('ai.memory.restored'))
    } catch (error: unknown) {
      toast.error(fehlermeldung(error, 'ai.memory.errors.restore'))
    } finally { setBusy(false) }
  }

  const allesEntfernen = async () => {
    if (!await confirm({
      title: t('ai.memory.clearTitle'),
      message: t('ai.memory.clearConfirm', { count: loeschbar }),
      confirmText: t('common.delete'), danger: true,
    })) return
    setBusy(true)
    try {
      const { removed } = await aiApi.clearMemory(memoryScopeName(scope), teamId, serverId)
      formLeeren()
      await nachAenderung(1)
      toast.success(t('ai.memory.cleared', { count: removed }))
    } catch (error: unknown) {
      toast.error(fehlermeldung(error, 'ai.memory.errors.delete'))
    } finally { setBusy(false) }
  }

  const bearbeiten = (entry: AiMemoryEntry) => {
    setText(entry.value)
    setTitel(entry.titel ?? '')
    setThema(entry.thema?.name ?? '')
    setBearbeitet(entry)
    // Beim Bearbeiten den Eintrag aufklappen
    setExpandedIds((prev) => new Set(prev).add(entry.id))
    setTimeout(() => {
      if (typeof formRef.current?.scrollIntoView === 'function') {
        formRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }
      textRef.current?.focus()
    }, 50)
  }

  const verlaufOeffnen = async (entry: AiMemoryEntry) => {
    setBusy(true)
    try {
      setVerlauf({ entry, fassungen: await aiApi.listMemoryVersions(entry.id) })
    } catch { toast.error(t('ai.memory.errors.history')) } finally { setBusy(false) }
  }

  const fassungZurueckholen = async (fassungId: string) => {
    if (verlauf === null) return
    setVerlaufHolt(fassungId)
    try {
      await aiApi.restoreMemoryVersion(verlauf.entry.id, fassungId, verlauf.entry.fassung)
      setVerlauf(null)
      if (bearbeitet?.id === verlauf.entry.id) formLeeren()
      await nachAenderung()
      toast.success(t('ai.memory.versionRestored'))
    } catch (error: unknown) {
      toast.error(fehlermeldung(error, 'ai.memory.errors.save'))
      if (error instanceof SanitizedApiError && error.status === 409) {
        // Der Verlauf gehört zu einem Stand, den es nicht mehr gibt. Er geht
        // zu, die Liste kommt neu; beim nächsten Öffnen gilt die frische Fassung.
        setVerlauf(null)
        await nachAenderung().catch(() => null)
      }
    } finally { setVerlaufHolt(null) }
  }

  const herkunftsFilter = (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('ai.memory.filterOrigin')}>
      {(['all', 'user', 'ai'] as const).map((wert) => (
        <button
          key={wert}
          type="button"
          aria-pressed={herkunft === wert}
          onClick={() => setHerkunft(wert)}
          className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
            herkunft === wert
              ? 'border-primary/60 bg-primary/15 text-primary font-medium'
              : 'border-outline-variant/40 text-on-surface-variant hover:text-on-surface'
          }`}
        >
          {t(`ai.memory.origins.${wert}`)}
        </button>
      ))}
    </div>
  )

  const themenFilter = imAktuellen && themenGruppen.length > 0 && (
    <Dropdown
      value={themaWahl}
      onChange={themaWaehlen}
      disabled={busy}
      searchable={themenGruppen.length > 8}
      aria-label={t('ai.memory.themaFilter')}
      className="w-full sm:w-56"
      options={[
        { value: '', label: t('ai.memory.alleThemen') },
        ...themenGruppen.map((gruppe) => ({
          value: gruppe.schluessel, label: gruppe.name, hint: String(gruppe.anzahl),
        })),
      ]}
    />
  )

  const leerText = imAktuellen
    ? (entries.length === 0 ? t(`ai.memory.empty.${scope.kind}`) : t('ai.memory.noMatches'))
    : (entries.length === 0 ? t('ai.memory.emptyForgotten') : t('ai.memory.noMatches'))

  return (
    <AiKnowledgeShell
      icon={Brain}
      title={t(`ai.memory.titles.${scope.kind}`)}
      description={t(`ai.memory.descriptions.${scope.kind}`)}
      headerAction={scope.kind === 'user' ? (
        <label className="flex min-h-10 items-center gap-2.5 text-sm text-on-surface-variant">
          <span>{t('ai.memory.enabled')}</span>
          <Switch
            checked={enabled}
            disabled={busy}
            onCheckedChange={(next) => {
              setBusy(true)
              void aiApi.setMemoryPreference(next)
                .then(() => setEnabled(next))
                .catch(() => toast.error(t('ai.memory.errors.save')))
                .finally(() => setBusy(false))
            }}
            aria-label={t('ai.memory.enabled')}
          />
        </label>
      ) : undefined}
      note={!imAktuellen
        ? t('ai.memory.forgottenHint')
        : scope.kind === 'user'
          ? t('ai.memory.enabledScopeHint')
          : scope.kind === 'server_shared'
            ? t('ai.memory.serverSharedHint')
            : undefined}
      search={werkzeugleiste
        ? {
          value: suche,
          onChange: setSuche,
          label: seitenzahl > 1 ? t('ai.memory.searchPage') : t('ai.memory.search'),
        }
        : undefined}
      filters={(
        <>
          <TabBar
            tabs={ANSICHTEN}
            active={ansicht}
            onChange={ansichtWechseln}
            ariaLabel={t('ai.memory.ansichtLabel')}
            embedded
          />
          {themenFilter}
          {werkzeugleiste && herkunftsFilter}
          {sichtbar.length > 1 && (
            <Kurzinfo text={allExpanded ? t('ai.memory.collapseAll') : t('ai.memory.expandAll')} className="sm:!hidden">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={toggleAllExpanded}
                aria-label={allExpanded ? t('ai.memory.collapseAll') : t('ai.memory.expandAll')}
              >
                <ChevronsUpDown className="h-4 w-4" aria-hidden="true" />
                <span className="hidden sm:inline">
                  {allExpanded ? t('ai.memory.collapseAll') : t('ai.memory.expandAll')}
                </span>
              </Button>
            </Kurzinfo>
          )}
          {darfAendern && imAktuellen && (
            <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => setImportOffen(true)}>
              <Download className="h-4 w-4" aria-hidden="true" />
              {t('ai.memory.import.button')}
            </Button>
          )}
          {darfAendern && imAktuellen && entries.length > 0 && (
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void allesEntfernen()}>
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t('ai.memory.clearAll')}
            </Button>
          )}
        </>
      )}
      count={werkzeugleiste
        ? (seitenzahl > 1
          ? t('ai.memory.countPage', { shown: sichtbar.length, total: entries.length })
          : t('ai.memory.count', { shown: sichtbar.length, total: entries.length }))
        : undefined}
    >
      {darfAendern && imAktuellen && (
        <form
          ref={formRef}
          className="rounded-xl border border-outline-variant/40 bg-surface-container-low/40 p-3 sm:p-4 space-y-3"
          onSubmit={speichern}
        >
          <div className="flex items-center gap-2 text-xs font-semibold text-on-surface-variant">
            <BookOpen className="h-4 w-4 text-primary" aria-hidden="true" />
            <span>{bearbeitet !== null ? t('ai.memory.editEntry') : t('ai.memory.newEntry')}</span>
          </div>

          <Textarea
            ref={textRef}
            rows={2}
            className="min-h-16"
            maxLength={2000}
            value={text}
            disabled={busy}
            onChange={(event) => setText(event.target.value)}
            placeholder={t('ai.memory.textPlaceholder')}
            aria-label={t('ai.memory.text')}
          />
          <div className="grid gap-2.5 sm:grid-cols-2">
            <Input
              maxLength={120}
              value={titel}
              disabled={busy}
              onChange={(event) => setTitel(event.target.value)}
              placeholder={t('ai.memory.titel')}
              aria-label={t('ai.memory.titel')}
            />
            <Input
              maxLength={60}
              value={thema}
              disabled={busy}
              onChange={(event) => setThema(event.target.value)}
              placeholder={t('ai.memory.thema')}
              aria-label={t('ai.memory.thema')}
            />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
            <div className="flex items-center gap-2">
              <Button type="submit" size="sm" disabled={busy || !text.trim()}>
                {bearbeitet === null ? (
                  <Plus className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <Save className="h-4 w-4" aria-hidden="true" />
                )}
                {bearbeitet === null ? t('ai.memory.add') : t('settings.save')}
              </Button>
              {bearbeitet !== null && (
                <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={formLeeren}>
                  <X className="h-4 w-4" aria-hidden="true" />
                  {t('common.cancel')}
                </Button>
              )}
            </div>

            <span className="inline-flex items-center gap-1.5 text-label-sm text-on-surface-variant/80">
              <ShieldAlert className="h-3.5 w-3.5 text-secondary shrink-0" aria-hidden="true" />
              {t('ai.memory.secretHint')}
            </span>
          </div>
        </form>
      )}

      {/* Logbuch-Einträge Liste mit Accordion & Virtual/Lazy Scrolling */}
      <div className="space-y-2">
        {displayEntries.map((entry) => {
          const isExpanded = expandedIds.has(entry.id)
          const isEditing = bearbeitet?.id === entry.id
          const kopf = ueberschrift(entry)
          const name = benennung(entry)

          return (
            <article
              key={entry.id}
              className={`rounded-xl border transition-all duration-150 overflow-hidden ${
                isEditing
                  ? 'border-primary/60 bg-primary/5 shadow-sm'
                  : isExpanded
                    ? 'border-outline-variant/60 bg-surface-container-low/50 shadow-sm'
                    : 'border-outline-variant/30 bg-surface-container-lowest/60 hover:border-outline-variant/60 hover:bg-surface-container-low/30'
              } ${
                entry.origin === 'ai' ? 'border-l-4 border-l-primary/70' : 'border-l-4 border-l-secondary/70'
              }`}
            >
              {/* Kompakte Kopfzeile / Eingeklappte Notiz-Zeile */}
              <div className="flex items-center justify-between gap-2 p-3">
                <button
                  type="button"
                  aria-expanded={isExpanded}
                  onClick={() => toggleExpand(entry.id)}
                  className="flex items-center gap-2.5 min-w-0 flex-1 text-left cursor-pointer select-none group focus:outline-none"
                >
                  <span className="text-on-surface-variant/70 shrink-0 group-hover:text-primary transition-colors">
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4 text-primary" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="h-4 w-4" aria-hidden="true" />
                    )}
                  </span>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Ohne Titel ist der Satz selbst die Zeile. */}
                      <span className={`text-xs sm:text-sm font-medium text-on-surface group-hover:text-primary transition-colors ${
                        kopf === null && !isExpanded ? 'line-clamp-2' : ''
                      }`}
                      >
                        {kopf ?? (isExpanded ? kuerzen(entry.value) : entry.value)}
                      </span>

                      {entry.thema && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-outline-variant/40 bg-surface-container px-2 py-0.5 text-label-sm text-on-surface-variant">
                          <Tag className="h-3 w-3 shrink-0" aria-hidden="true" />
                          {entry.thema.name}
                        </span>
                      )}

                      {/* Server-Zugehörigkeit */}
                      {entry.scope === 'server' && entry.server_id !== null && (
                        <span className="rounded-full border border-outline-variant/40 bg-surface-container px-2 py-0.5 text-label-sm text-on-surface-variant">
                          {t('ai.memory.forServer', {
                            name: serverNamen.get(entry.server_id) ?? `#${entry.server_id}`,
                          })}
                        </span>
                      )}

                      {/* KI-Herkunftsbadge */}
                      {entry.origin === 'ai' && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-label-sm text-primary">
                          <BrainCircuit className="h-3 w-3" aria-hidden="true" />
                          {t('ai.memory.originAi')}
                        </span>
                      )}

                      {/* Häufigkeitszähler */}
                      {entry.use_count > 0 && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-outline-variant/40 bg-surface-container-high px-2 py-0.5 text-label-sm text-on-surface-variant font-medium">
                          <Flame className="h-3 w-3 text-status-warning shrink-0" aria-hidden="true" />
                          {t('ai.memory.usedCount', { count: entry.use_count })}
                        </span>
                      )}

                      {/* Gemerkt-Datum (dezent) */}
                      {entry.created_at && (
                        <span className="hidden md:inline-flex items-center gap-1 text-label-sm text-on-surface-variant/70">
                          <Calendar className="h-3 w-3 shrink-0" aria-hidden="true" />
                          {formatMemoryDate(entry.created_at, i18n.language)}
                        </span>
                      )}
                    </div>

                    {/* Auszug des Satzes im eingeklappten Zustand, wenn oben ein Titel steht */}
                    {!isExpanded && kopf !== null && (
                      <p className="mt-0.5 truncate text-xs text-on-surface-variant max-w-md sm:max-w-xl">
                        {entry.value}
                      </p>
                    )}
                  </div>
                </button>

                {darfAendern && (
                  <div className="flex shrink-0 items-center gap-1">
                    {imAktuellen ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => bearbeiten(entry)}
                        aria-label={`${t('ai.memory.edit')}: ${name}`}
                      >
                        <Pencil className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => void zurueckholen(entry)}
                        aria-label={`${t('ai.memory.restore')}: ${name}`}
                      >
                        <RotateCcw className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    )}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => void entfernen(entry)}
                      aria-label={`${t(imAktuellen ? 'ai.memory.delete' : 'ai.memory.deleteForever')}: ${name}`}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </Button>
                  </div>
                )}
              </div>

              {/* Ausgeklappte Logbuch-Ansicht */}
              {isExpanded && (
                <div className="border-t border-outline-variant/20 bg-surface-container-low/30 px-3.5 pb-3.5 pt-2 space-y-3">
                  <div className="rounded-lg border border-outline-variant/30 bg-surface-container-lowest/90 p-3 text-sm text-on-surface whitespace-pre-wrap break-words leading-relaxed">
                    {entry.value}
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-on-surface-variant border-t border-outline-variant/20 pt-2">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span>{t(`ai.memory.quellen.${entry.quelle}`)}</span>
                      {entry.key && (
                        <span className="font-mono text-label-sm text-on-surface-variant/80">
                          {t('ai.memory.rawKey', { key: entry.key })}
                        </span>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      {entry.created_at && (
                        <span>
                          {t('ai.memory.rememberedAt', { date: formatMemoryDate(entry.created_at, i18n.language) })}
                        </span>
                      )}
                      {entry.last_used_at && (
                        <span>
                          {t('ai.memory.lastUsedAt', { date: formatMemoryDate(entry.last_used_at, i18n.language) })}
                        </span>
                      )}
                      {entry.vergessen_am && (
                        <span>
                          {t('ai.memory.forgottenUntil', {
                            date: formatMemoryDate(entry.vergessen_am, i18n.language),
                            until: formatMemoryDate(
                              new Date(Date.parse(entry.vergessen_am) + VERGESSEN_TAGE * 86_400_000).toISOString(),
                              i18n.language,
                            ),
                          })}
                        </span>
                      )}
                      {/* Ohne Änderung gibt es keine frühere Fassung. */}
                      {entry.fassung > 1 && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          disabled={busy}
                          onClick={() => void verlaufOeffnen(entry)}
                          aria-label={`${t('ai.memory.history')}: ${name}`}
                        >
                          <History className="h-4 w-4" aria-hidden="true" />
                          {t('ai.memory.history')}
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </article>
          )
        })}

        {/* Sentinel für Lazy Loading */}
        {displayEntries.length < sichtbar.length && (
          <div ref={sentinelRef} className="h-2 w-full" aria-hidden="true" />
        )}

        {/* Virtuelles/Lazy Nachladen: Fortschritt und Schaltfläche */}
        {displayEntries.length < sichtbar.length && (
          <div className="flex items-center justify-between rounded-lg border border-dashed border-outline-variant/40 px-3 py-2 text-xs text-on-surface-variant">
            <span>
              {t('ai.memory.showingCount', {
                shown: displayEntries.length,
                total: sichtbar.length,
              })}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setRenderedLimit((prev) => Math.min(prev + CHUNK_SIZE, sichtbar.length))}
            >
              {t('ai.memory.loadMore')}
            </Button>
          </div>
        )}

        {sichtbar.length === 0 && (
          <p className="rounded-xl border border-dashed border-outline-variant/50 px-4 py-5 text-sm text-on-surface-variant text-center">
            {leerText}
          </p>
        )}
      </div>

      {darfAendern && (
        <AiMemoryImportModal
          open={importOffen}
          onOpenChange={setImportOffen}
          scope={scope}
          onImported={async () => {
            try { await nachAenderung(1) } catch { toast.error(t('ai.memory.errors.load')) }
          }}
        />
      )}

      <Dialog open={verlauf !== null} onOpenChange={(offen) => { if (!offen && verlaufHolt === null) setVerlauf(null) }}>
        <DialogContent className="max-w-xl" aria-labelledby="ai-memory-verlauf-titel">
          <DialogHeader className="pr-14">
            <DialogTitle id="ai-memory-verlauf-titel" className="flex items-center gap-2">
              <History className="h-5 w-5" aria-hidden="true" />
              {t('ai.memory.historyTitle')}
            </DialogTitle>
            <DialogDescription>{t('ai.memory.historyDescription')}</DialogDescription>
          </DialogHeader>
          <div className="px-4 py-4 sm:px-6">
            {verlauf !== null && (
              <Versionsliste
                versionen={verlauf.fassungen.map((fassung) => ({
                  id: fassung.id,
                  zeit: Date.parse(fassung.erstellt),
                  text: fassung.titel ? `${fassung.titel} — ${fassung.text}` : fassung.text,
                  hinweis: t(`ai.memory.gruende.${fassung.grund}`),
                }))}
                onWiederherstellen={darfAendern && verlauf.entry.status === 'aktiv'
                  ? (id) => void fassungZurueckholen(id)
                  : undefined}
                laeuft={verlaufHolt}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      <Pagination
        page={seite}
        pageCount={seitenzahl}
        label={t('ai.memory.total', { count: gesamt })}
        disabled={busy}
        onChange={blaettern}
      />
    </AiKnowledgeShell>
  )
}
