import { Brain, BrainCircuit, Pencil, Plus, Save, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiMemoryEntry } from '@/api/ai'
import { api, SanitizedApiError } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Switch } from '@/Singra/UI'
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

type Herkunft = 'all' | 'user' | 'ai'

interface ServerOption {
  id: number
  name: string
}

interface Props {
  /** Ohne Angabe: das eigene Gedächtnis. */
  scope?: AiKnowledgeScope
}

/**
 * Erinnerungen einsehen und pflegen — eigene oder die eines Teams.
 *
 * Vorher war das eine flache Liste mit einem Formular darunter. Das trägt, bis
 * die KI zwanzig Einträge abgeleitet hat; danach findet man nichts mehr und
 * kann nur noch einzeln löschen. Deshalb: Suche, Herkunftsfilter, Bearbeiten an
 * Ort und Stelle und ein „alles löschen" für den Fall, dass man wirklich
 * aufräumen will.
 *
 * Die Herkunft ist der wichtigste Filter, nicht ein hübsches Extra: „was hat
 * sich die KI über mich gemerkt?" ist eine andere Frage als „was habe ich ihr
 * gesagt?", und nur die erste beantwortet niemand sonst.
 */
export function AiMemoryManager({ scope = { kind: 'user' } }: Props) {
  const { t } = useTranslation()
  const allowed = useHasPermission('ai.memory.use')
  const darfAendern = scopeCanManage(scope)
  const teamId = scopeTeamId(scope)
  const serverId = scopeServerId(scope)

  const [entries, setEntries] = useState<AiMemoryEntry[]>([])
  const [enabled, setEnabled] = useState(false)
  const [suche, setSuche] = useState('')
  const [herkunft, setHerkunft] = useState<Herkunft>('all')
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  // Nicht nur die id, sondern der ganze Eintrag: beim Speichern zählt der
  // Bereich des Eintrags, nicht der der Ansicht. Der persönliche Bereich listet
  // bewusst auch die Notizen zu einzelnen Servern mit — wer davon nur die id
  // festhält, schickt sie beim Ändern als `scope: 'user'` zurück und legt damit
  // eine Kopie an, statt sie zu ändern.
  const [bearbeitet, setBearbeitet] = useState<AiMemoryEntry | null>(null)
  const [serverNamen, setServerNamen] = useState<Map<number, string>>(new Map())
  const [busy, setBusy] = useState(false)

  // Der persönliche Bereich holt beides auf einmal: allgemeine Einträge und
  // die Notizen zu einzelnen Servern. Beides gehört dem Benutzer, beides geht
  // in jedes Gespräch — und die serverbezogenen waren bisher nirgends sichtbar,
  // weil `listMemory('server', …)` je Aufruf einen konkreten Server verlangt.
  const laden = async () => {
    if (scope.kind === 'user') return setEntries(await aiApi.listPersonalMemory())
    if (scope.kind === 'panel') return setEntries(await aiApi.listMemory('panel'))
    if (scope.kind === 'server_shared') {
      return setEntries(await aiApi.listMemory('server_shared', scope.serverId))
    }
    setEntries(await aiApi.listMemory('team', undefined, scope.teamId))
  }

  useEffect(() => {
    if (!allowed) return
    let active = true
    setSuche(''); setHerkunft('all'); setKey(''); setValue(''); setBearbeitet(null)
    const liste = scope.kind === 'user'
      ? aiApi.listPersonalMemory()
      : scope.kind === 'panel'
        ? aiApi.listMemory('panel')
        : scope.kind === 'server_shared'
          ? aiApi.listMemory('server_shared', scope.serverId)
          : aiApi.listMemory('team', undefined, scope.teamId)
    Promise.all([
      liste,
      // Der Schalter ist eine Einstellung des Benutzers, kein Merkmal des
      // Bereichs — er wird auch in der Teamansicht geladen, aber nur in der
      // persönlichen angezeigt.
      aiApi.getMemoryPreference(),
      // Nur für die Beschriftung der Servernotizen. Fällt sie aus, steht dort
      // die Nummer statt des Namens — kein Grund, die Liste scheitern zu lassen.
      scope.kind === 'user'
        ? api<ServerOption[]>('/servers').catch(() => [] as ServerOption[])
        : Promise.resolve([] as ServerOption[]),
    ])
      .then(([rows, preference, servers]) => {
        if (!active) return
        setEntries(rows)
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
        || entry.value.toLowerCase().includes(nadel))
      // Zuletzt genutzt zuerst: was die KI gerade heranzieht, ist das, was man
      // prüfen will. Nie genutzte Einträge stehen hinten, nach Schlüssel sortiert.
      .sort((a, b) => {
        const links = a.last_used_at ? Date.parse(a.last_used_at) : 0
        const rechts = b.last_used_at ? Date.parse(b.last_used_at) : 0
        return rechts - links || a.key.localeCompare(b.key)
      })
  }, [entries, herkunft, suche])

  // Suche, Herkunftsfilter und Zähler erscheinen erst, wenn die Liste lang genug
  // ist, um sie zu brauchen — aber sie verschwinden nicht, solange sie noch
  // etwas bewirken. Genau das war der Fehler: löschte man aus vier Einträgen den
  // einzigen Suchtreffer, hängte die Schranke das Suchfeld ab, während `sichtbar`
  // weiter nach dem alten Wort filterte. Übrig blieb „Kein Eintrag passt zur
  // Suche" ohne ein Bedienelement, mit dem man da wieder herauskommt — half nur
  // ein Bereichswechsel oder Neuladen. Eine Bedingung für alle drei, damit sie
  // nicht wieder auseinanderlaufen.
  const werkzeugleiste = entries.length > 3 || suche !== '' || herkunft !== 'all'

  if (!allowed) return null

  const speichern = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!key.trim() || !value.trim() || busy) return
    setBusy(true)
    try {
      // Beim Ändern gilt der Bereich des Eintrags, beim Anlegen der der Ansicht.
      //
      // Der persönliche Bereich zeigt auch die Notizen zu einzelnen Servern; die
      // liegen unter `server:{sid}:user:{uid}`. Ging eine Korrektur daran als
      // `scope: 'user'` hinaus, suchte das Backend unter `user:{uid}`, fand dort
      // nichts und legte eine zweite Zeile mit demselben Schlüssel an: die alte
      // Notiz wirkte unverändert weiter, und ab da gingen beide Werte gemeinsam
      // ins Gespräch über diesen Server. Dass eine Änderung an Ort und Stelle
      // gemeint ist, sagt schon das gesperrte Schlüsselfeld weiter unten.
      //
      // Von Hand angelegt wird dagegen immer im Bereich selbst, nie
      // serverbezogen: eine Notiz „zu diesem Server" entsteht im Gespräch über
      // ihn, und ein Serverfeld im Formular wäre ein zweiter Weg mit eigenen
      // Fehlerquellen.
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
    // Die Anzahl steht in der Frage: „alles löschen" ist bei drei Einträgen
    // etwas anderes als bei achtzig, und nur der Benutzer weiß, welches er
    // gerade meint.
    //
    // Gezählt wird, was der Knopf danach wirklich trifft — nicht, was die Liste
    // zeigt. Der persönliche Bereich listet auch die Notizen zu einzelnen
    // Servern mit, gelöscht werden aber nur die allgemeinen Einträge (siehe
    // unten). Mit `entries.length` fragte die Bestätigung nach „alle 12" und
    // meldete danach „8 gelöscht".
    const betroffen = entries.filter((eintrag) => eintrag.scope === memoryScopeName(scope))
    if (!await confirm({
      title: t('ai.memory.clearTitle'),
      message: t('ai.memory.clearConfirm', { count: betroffen.length }),
      confirmText: t('common.delete'), danger: true,
    })) return
    setBusy(true)
    try {
      // Bewusst nur der Bereich selbst: „alles löschen" im Profil räumt die
      // allgemeinen Einträge, nicht die Notizen zu einzelnen Servern. Die
      // stehen sichtbar daneben und lassen sich einzeln entfernen — ein Knopf,
      // der beides mitnimmt, wäre weiter, als er aussieht.
      const { removed } = await aiApi.clearMemory(memoryScopeName(scope), teamId, serverId)
      setKey(''); setValue(''); setBearbeitet(null)
      await laden()
      toast.success(t('ai.memory.cleared', { count: removed }))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memory.errors.delete'))
    } finally { setBusy(false) }
  }

  const bearbeiten = (entry: AiMemoryEntry) => {
    setKey(entry.key); setValue(entry.value); setBearbeitet(entry)
  }

  const herkunftsFilter = (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('ai.memory.filterOrigin')}>
      {(['all', 'user', 'ai'] as const).map((wert) => (
        <button
          key={wert}
          type="button"
          aria-pressed={herkunft === wert}
          onClick={() => setHerkunft(wert)}
          className={`rounded-full border px-3 py-1.5 text-xs transition-colors ${
            herkunft === wert
              ? 'border-primary/60 bg-primary/15 text-primary'
              : 'border-outline-variant/50 text-on-surface-variant hover:text-on-surface'
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
      // Die Überschrift folgt dem Bereich. Sie lautete auch über dem Wissen
      // eines Teams „Persönliches KI-Memory" — genau die Verwechslung, die
      // persönlich und geteilt zusammenrühren lässt.
      title={t(`ai.memory.titles.${scope.kind}`)}
      description={t(`ai.memory.descriptions.${scope.kind}`)}
      headerAction={scope.kind === 'user' ? (
        <label className="flex min-h-10 items-center gap-3 text-sm text-on-surface-variant">
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
      // Der Schalter regiert nur diesen Bereich. Das stand nirgends und war
      // auch nicht so — bis eben nahm er dem Assistenten auch das Teamwissen.
      //
      // Beim Serverwissen steht dafür eine andere Ansage: dass es geteilt ist.
      // Wer hier etwas hinschreibt, soll vorher wissen, dass die Kollegen es
      // lesen — und nicht erst, wenn es jemand zitiert.
      note={scope.kind === 'user'
        ? t('ai.memory.enabledScopeHint')
        : scope.kind === 'server_shared'
          ? t('ai.memory.serverSharedHint')
          : undefined}
      search={werkzeugleiste
        ? { value: suche, onChange: setSuche, label: t('ai.memory.search') }
        : undefined}
      filters={(
        <>
          {werkzeugleiste && herkunftsFilter}
          {darfAendern && entries.length > 0 && (
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void allesEntfernen()}>
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t('ai.memory.clearAll')}
            </Button>
          )}
        </>
      )}
      count={werkzeugleiste
        ? t('ai.memory.count', { shown: sichtbar.length, total: entries.length })
        : undefined}
    >
      <div className="space-y-2">
        {sichtbar.map((entry) => (
          <div
            key={entry.id}
            className={`flex items-start gap-3 rounded-xl border p-3 transition-colors ${
              bearbeitet?.id === entry.id
                ? 'border-primary/50 bg-primary/5'
                : 'border-outline-variant/40 bg-surface-container-low/35'
            }`}
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-mono text-xs font-semibold text-primary">{entry.key}</p>
                {/* Zu welchem Server die Notiz gehört. Ohne diese Angabe stünde
                    „startet nur mit erhöhtem Timeout" ohne Bezug zwischen den
                    allgemeinen Einträgen — dieselbe Verwechslungsgefahr, die
                    beim Modell die Server-ID in der Zeile verhindert. */}
                {entry.scope === 'server' && entry.server_id !== null && (
                  <span className="rounded-full border border-outline-variant/50 px-2 py-0.5 text-[10px] text-on-surface-variant">
                    {t('ai.memory.forServer', {
                      name: serverNamen.get(entry.server_id) ?? `#${entry.server_id}`,
                    })}
                  </span>
                )}
                {/* Herkunft sichtbar machen: niemand soll raten müssen, ob er
                    das selbst hinterlegt hat oder ob die KI es abgeleitet hat. */}
                {entry.origin === 'ai' && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-outline-variant/50 px-2 py-0.5 text-[10px] text-on-surface-variant">
                    <BrainCircuit className="h-3 w-3" aria-hidden="true" />
                    {t('ai.memory.originAi')}
                  </span>
                )}
                {entry.use_count > 0 && (
                  <span className="text-[10px] text-on-surface-variant/70">
                    {t('ai.memory.usedCount', { count: entry.use_count })}
                  </span>
                )}
              </div>
              <p className="mt-1 whitespace-pre-wrap break-words text-sm text-on-surface-variant">{entry.value}</p>
            </div>
            {darfAendern && (
              <div className="flex shrink-0 gap-1">
                <Button
                  type="button" size="sm" variant="ghost" disabled={busy}
                  onClick={() => bearbeiten(entry)}
                  aria-label={`${t('ai.memory.edit')}: ${entry.key}`}
                >
                  <Pencil className="h-4 w-4" aria-hidden="true" />
                </Button>
                <Button
                  type="button" size="sm" variant="ghost" disabled={busy}
                  onClick={() => void entfernen(entry)}
                  aria-label={`${t('ai.memory.delete')}: ${entry.key}`}
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            )}
          </div>
        ))}
        {sichtbar.length === 0 && (
          <p className="rounded-xl border border-dashed border-outline-variant/50 px-4 py-5 text-sm text-on-surface-variant">
            {entries.length === 0 ? t('ai.memory.empty') : t('ai.memory.noMatches')}
          </p>
        )}
      </div>

      {darfAendern && (
        <form className="space-y-3 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4" onSubmit={speichern}>
          <div className="grid gap-3 md:grid-cols-[14rem_minmax(0,1fr)]">
            <label className="space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.memory.key')}
              </span>
              <input
                className="msm-input" pattern="[A-Za-z0-9_.-]+" maxLength={64}
                value={key} disabled={busy || bearbeitet !== null}
                onChange={(event) => setKey(event.target.value)}
                placeholder="ram-praeferenz"
                aria-label={t('ai.memory.key')}
              />
            </label>
            <label className="space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.memory.value')}
              </span>
              <input
                className="msm-input" maxLength={2000} value={value} disabled={busy}
                onChange={(event) => setValue(event.target.value)}
                aria-label={t('ai.memory.value')}
              />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={busy || !key.trim() || !value.trim()}>
              {bearbeitet === null
                ? <Plus className="h-4 w-4" aria-hidden="true" />
                : <Save className="h-4 w-4" aria-hidden="true" />}
              {bearbeitet === null ? t('ai.memory.add') : t('settings.save')}
            </Button>
            {bearbeitet !== null && (
              <Button
                type="button" variant="secondary" disabled={busy}
                onClick={() => { setKey(''); setValue(''); setBearbeitet(null) }}
              >
                <X className="h-4 w-4" aria-hidden="true" />
                {t('common.cancel')}
              </Button>
            )}
            <span className="text-xs text-on-surface-variant">{t('ai.memory.secretHint')}</span>
          </div>
        </form>
      )}
    </AiKnowledgeShell>
  )
}
