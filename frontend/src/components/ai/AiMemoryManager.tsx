import {
  BookOpen,
  Brain,
  BrainCircuit,
  Calendar,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  Flame,
  Pencil,
  Plus,
  Save,
  ShieldAlert,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiMemoryEntry, type AiMemoryPage } from '@/api/ai'
import { api, SanitizedApiError } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Pagination, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

import { AiKnowledgeShell } from './AiKnowledgeShell'
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

/**
 * Erinnerungen einsehen und pflegen im kompakten Logbuch-Stil.
 *
 * Zeigt Einträge standardmäßig eingeklappt als Notizbuch-Zeilen mit
 * formatierten Titeln, diskreten Metadaten (Gemerkt am, Häufigkeit)
 * und progressivem Lazy-Scrolling für optimale Performance auch bei
 * Hunderten Einträgen über Web, Desktop (Tauri) und Mobile hinweg.
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
  const [enabled, setEnabled] = useState(false)
  const [suche, setSuche] = useState('')
  const [herkunft, setHerkunft] = useState<Herkunft>('all')
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [bearbeitet, setBearbeitet] = useState<AiMemoryEntry | null>(null)
  const [serverNamen, setServerNamen] = useState<Map<number, string>>(new Map())
  const [busy, setBusy] = useState(false)

  // Accordion-Zustand: Standardmäßig alle eingeklappt
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  // Lazy Loading / Progressiver Nachlademechanismus für flüssiges Scrollen
  const [renderedLimit, setRenderedLimit] = useState(CHUNK_SIZE)
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const formRef = useRef<HTMLFormElement | null>(null)
  const valueInputRef = useRef<HTMLInputElement | null>(null)

  const holen = (offset: number): Promise<AiMemoryPage> => (
    scope.kind === 'user'
      ? aiApi.listPersonalMemory(offset)
      : aiApi.listScopeMemory(memoryScopeName(scope), serverId, teamId, offset)
  )

  const uebernehmen = (ladung: AiMemoryPage, zielSeite: number) => {
    setEntries(ladung.entries)
    setGesamt(ladung.total)
    setLoeschbar(ladung.clearable)
    setSeitengroesse(ladung.limit)
    setSeite(zielSeite)
  }

  const laden = async (zielSeite = seite, groesse = seitengroesse) => {
    const ladung = await holen(Math.max(0, zielSeite - 1) * groesse)
    const letzte = Math.max(1, Math.ceil(ladung.total / ladung.limit))
    if (zielSeite > letzte) return laden(letzte, ladung.limit)
    uebernehmen(ladung, zielSeite)
  }

  useEffect(() => {
    if (!allowed) return
    let active = true
    setSuche(''); setHerkunft('all'); setKey(''); setValue(''); setBearbeitet(null)
    setExpandedIds(new Set())
    Promise.all([
      holen(0),
      aiApi.getMemoryPreference(),
      scope.kind === 'user'
        ? api<ServerOption[]>('/servers').catch(() => [] as ServerOption[])
        : Promise.resolve([] as ServerOption[]),
    ])
      .then(([ladung, preference, servers]) => {
        if (!active) return
        uebernehmen(ladung, 1)
        setEnabled(preference.enabled)
        setServerNamen(new Map(servers.map((row) => [row.id, row.name])))
      })
      .catch(() => { if (active) toast.error(t('ai.memory.errors.load')) })
    return () => { active = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, scope.kind, teamId, serverId, t])

  const sichtbar = useMemo(() => {
    const nadel = suche.trim().toLowerCase()
    return entries
      .filter((entry) => herkunft === 'all' || entry.origin === herkunft)
      .filter((entry) => !nadel
        || entry.key.toLowerCase().includes(nadel)
        || formatMemoryKey(entry.key).toLowerCase().includes(nadel)
        || entry.value.toLowerCase().includes(nadel))
      .sort((a, b) => {
        const links = a.last_used_at ? Date.parse(a.last_used_at) : 0
        const rechts = b.last_used_at ? Date.parse(b.last_used_at) : 0
        return rechts - links || a.key.localeCompare(b.key)
      })
  }, [entries, herkunft, suche])

  // Zurücksetzen des Lazy-Loading-Limits beim Filtern
  useEffect(() => {
    setRenderedLimit(CHUNK_SIZE)
  }, [suche, herkunft, seite])

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

  const werkzeugleiste = entries.length > 3 || suche !== '' || herkunft !== 'all'
  const seitenzahl = seitengroesse > 0 ? Math.max(1, Math.ceil(gesamt / seitengroesse)) : 1

  if (!allowed) return null

  const blaettern = (naechste: number) => {
    setBusy(true)
    void laden(naechste)
      .catch(() => toast.error(t('ai.memory.errors.load')))
      .finally(() => setBusy(false))
  }

  const speichern = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!key.trim() || !value.trim() || busy) return
    setBusy(true)
    try {
      const feld = { key: key.trim(), value: value.trim() }
      await aiApi.saveMemory(
        bearbeitet?.scope === 'server' && bearbeitet.server_id !== null
          ? { scope: 'server', server_id: bearbeitet.server_id, ...feld }
          : scope.kind === 'team'
            ? { scope: 'team', team_id: scope.teamId, ...feld }
            : scope.kind === 'server_shared'
              ? { scope: 'server_shared', server_id: scope.serverId, ...feld }
              : { scope: scope.kind, ...feld },
      )
      setKey(''); setValue(''); setBearbeitet(null)
      await laden()
      toast.success(t('ai.memory.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memory.errors.save'))
    } finally { setBusy(false) }
  }

  const entfernen = async (entry: AiMemoryEntry) => {
    if (!await confirm({
      message: t('ai.memory.deleteConfirm', { key: entry.key }),
      confirmText: t('common.delete'), danger: true,
    })) return
    setBusy(true)
    try {
      await aiApi.deleteMemory(entry.id)
      if (bearbeitet?.id === entry.id) { setKey(''); setValue(''); setBearbeitet(null) }
      await laden()
    } catch { toast.error(t('ai.memory.errors.delete')) } finally { setBusy(false) }
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
      setKey(''); setValue(''); setBearbeitet(null)
      await laden(1)
      toast.success(t('ai.memory.cleared', { count: removed }))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memory.errors.delete'))
    } finally { setBusy(false) }
  }

  const bearbeiten = (entry: AiMemoryEntry) => {
    setKey(entry.key)
    setValue(entry.value)
    setBearbeitet(entry)
    // Beim Bearbeiten den Eintrag aufklappen
    setExpandedIds((prev) => new Set(prev).add(entry.id))
    setTimeout(() => {
      if (typeof formRef.current?.scrollIntoView === 'function') {
        formRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }
      valueInputRef.current?.focus()
    }, 50)
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
      note={scope.kind === 'user'
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
          {werkzeugleiste && herkunftsFilter}
          {sichtbar.length > 1 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={toggleAllExpanded}
              title={allExpanded ? t('ai.memory.collapseAll') : t('ai.memory.expandAll')}
              aria-label={allExpanded ? t('ai.memory.collapseAll') : t('ai.memory.expandAll')}
            >
              <ChevronsUpDown className="h-4 w-4" aria-hidden="true" />
              <span className="hidden sm:inline">
                {allExpanded ? t('ai.memory.collapseAll') : t('ai.memory.expandAll')}
              </span>
            </Button>
          )}
          {darfAendern && entries.length > 0 && (
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
      {/* Schneller, kompakter Logbuch-Eingabebereich direkt oben greifbar */}
      {darfAendern && (
        <form
          ref={formRef}
          className="rounded-xl border border-outline-variant/40 bg-surface-container-low/40 p-3 sm:p-4 space-y-3"
          onSubmit={speichern}
        >
          <div className="flex items-center gap-2 text-xs font-semibold text-on-surface-variant">
            <BookOpen className="h-4 w-4 text-primary" aria-hidden="true" />
            <span>{bearbeitet !== null ? t('ai.memory.editEntry') : t('ai.memory.newEntry')}</span>
          </div>

          <div className="grid gap-2.5 sm:grid-cols-[14rem_minmax(0,1fr)]">
            <label className="space-y-1">
              <span className="sr-only">{t('ai.memory.key')}</span>
              <input
                className="msm-input text-xs sm:text-sm"
                pattern="[A-Za-z0-9_.-]+"
                maxLength={64}
                value={key}
                disabled={busy || bearbeitet !== null}
                onChange={(event) => setKey(event.target.value)}
                placeholder={t('ai.memory.keyPlaceholder')}
                aria-label={t('ai.memory.key')}
              />
            </label>
            <label className="space-y-1">
              <span className="sr-only">{t('ai.memory.value')}</span>
              <input
                ref={valueInputRef}
                className="msm-input text-xs sm:text-sm"
                maxLength={2000}
                value={value}
                disabled={busy}
                onChange={(event) => setValue(event.target.value)}
                placeholder={t('ai.memory.valuePlaceholder')}
                aria-label={t('ai.memory.value')}
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
            <div className="flex items-center gap-2">
              <Button type="submit" size="sm" disabled={busy || !key.trim() || !value.trim()}>
                {bearbeitet === null ? (
                  <Plus className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <Save className="h-4 w-4" aria-hidden="true" />
                )}
                {bearbeitet === null ? t('ai.memory.add') : t('settings.save')}
              </Button>
              {bearbeitet !== null && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  onClick={() => { setKey(''); setValue(''); setBearbeitet(null) }}
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                  {t('common.cancel')}
                </Button>
              )}
            </div>

            <span className="inline-flex items-center gap-1.5 text-[11px] text-on-surface-variant/80">
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
          const formattedTitle = formatMemoryKey(entry.key)

          return (
            <article
              key={entry.id}
              className={`rounded-xl border transition-all duration-150 overflow-hidden ${
                isEditing
                  ? 'border-primary/60 bg-primary/5 shadow-xs'
                  : isExpanded
                    ? 'border-outline-variant/60 bg-surface-container-low/50 shadow-xs'
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
                      <span className="text-xs sm:text-sm font-medium text-on-surface group-hover:text-primary transition-colors">
                        {formattedTitle}
                      </span>

                      {/* Server-Zugehörigkeit */}
                      {entry.scope === 'server' && entry.server_id !== null && (
                        <span className="rounded-full border border-outline-variant/40 bg-surface-container px-2 py-0.5 text-[10px] text-on-surface-variant">
                          {t('ai.memory.forServer', {
                            name: serverNamen.get(entry.server_id) ?? `#${entry.server_id}`,
                          })}
                        </span>
                      )}

                      {/* KI-Herkunftsbadge */}
                      {entry.origin === 'ai' && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                          <BrainCircuit className="h-3 w-3" aria-hidden="true" />
                          {t('ai.memory.originAi')}
                        </span>
                      )}

                      {/* Häufigkeitszähler */}
                      {entry.use_count > 0 && (
                        <span
                          className="inline-flex items-center gap-1 rounded-full border border-outline-variant/40 bg-surface-container-high px-2 py-0.5 text-[10px] text-on-surface-variant font-medium"
                          title={t('ai.memory.usedCount', { count: entry.use_count })}
                        >
                          <Flame className="h-3 w-3 text-amber-500 shrink-0" aria-hidden="true" />
                          {t('ai.memory.usedCount', { count: entry.use_count })}
                        </span>
                      )}

                      {/* Gemerkt-Datum (dezent) */}
                      {entry.created_at && (
                        <span className="hidden md:inline-flex items-center gap-1 text-[10px] text-on-surface-variant/70">
                          <Calendar className="h-3 w-3 shrink-0" aria-hidden="true" />
                          {formatMemoryDate(entry.created_at, i18n.language)}
                        </span>
                      )}
                    </div>

                    {/* Auszug / Vorschau des Werts im eingeklappten Zustand */}
                    {!isExpanded && (
                      <p className="mt-0.5 truncate text-xs text-on-surface-variant max-w-md sm:max-w-xl">
                        {entry.value}
                      </p>
                    )}
                  </div>
                </button>

                {/* Aktionen (Bearbeiten / Löschen) */}
                {darfAendern && (
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => bearbeiten(entry)}
                      aria-label={`${t('ai.memory.edit')}: ${entry.key}`}
                    >
                      <Pencil className="h-4 w-4" aria-hidden="true" />
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => void entfernen(entry)}
                      aria-label={`${t('ai.memory.delete')}: ${entry.key}`}
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
                    <span className="font-mono text-[11px] text-on-surface-variant/80">
                      {t('ai.memory.rawKey', { key: entry.key })}
                    </span>

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
            {entries.length === 0 ? t('ai.memory.empty') : t('ai.memory.noMatches')}
          </p>
        )}
      </div>

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
