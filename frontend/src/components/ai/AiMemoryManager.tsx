import { Brain, BrainCircuit, Pencil, Plus, Save, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiMemoryEntry } from '@/api/ai'
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

type Herkunft = 'all' | 'user' | 'ai'

interface ServerOption {
  id: number
  name: string
}

/**
 * Ein Ladeergebnis, gleich aus welchem Bereich es kommt.
 *
 * Nur der persönliche Bereich blättert; die geteilten liefern weiterhin alles
 * auf einmal. Beides hier auf dieselbe Form zu bringen, ist billiger als zwei
 * Ansichten mit denselben Knöpfen — die Unterscheidung steckt danach in genau
 * einer Zahl: `seitengroesse` 0 heißt „kam auf einmal".
 */
interface Ladung {
  rows: AiMemoryEntry[]
  gesamt: number
  loeschbar: number
  seitengroesse: number
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
  // Die angezeigte Seite und die drei Zahlen, die nur der Server kennt. Der
  // persönliche Bereich darf 5.000 Einträge fassen, und jeder davon kostet beim
  // Öffnen einen eigenen Aufruf an den DIS-Sidecar — gemessen rund zehn
  // Sekunden für alle. Er kommt deshalb seitenweise. `seitengroesse` ist 0,
  // solange nichts geladen wurde und in allen Bereichen, die weiterhin auf
  // einmal kommen; daraus wird eine Seite, und die Blätterleiste bleibt weg.
  const [seite, setSeite] = useState(1)
  const [gesamt, setGesamt] = useState(0)
  const [loeschbar, setLoeschbar] = useState(0)
  const [seitengroesse, setSeitengroesse] = useState(0)
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

  // Der persönliche Bereich holt beides: allgemeine Einträge und die Notizen zu
  // einzelnen Servern. Beides gehört dem Benutzer, beides geht in jedes
  // Gespräch — und die serverbezogenen waren lange nirgends sichtbar, weil
  // `listMemory('server', …)` je Aufruf einen konkreten Server verlangt.
  //
  // Er ist zugleich der einzige, der blättert. Die geteilten Bereiche kommen
  // weiterhin auf einmal; für sie ist `gesamt` schlicht die Länge der Liste und
  // `loeschbar` dasselbe, weil eine Bereichsabfrage dort genau den Bereich
  // liefert, den „Alle löschen" auch abräumt.
  const holen = async (offset: number): Promise<Ladung> => {
    if (scope.kind === 'user') {
      const seiteDaten = await aiApi.listPersonalMemory(offset)
      return {
        rows: seiteDaten.entries,
        gesamt: seiteDaten.total,
        loeschbar: seiteDaten.clearable,
        seitengroesse: seiteDaten.limit,
      }
    }
    const rows = scope.kind === 'panel'
      ? await aiApi.listMemory('panel')
      : scope.kind === 'server_shared'
        ? await aiApi.listMemory('server_shared', scope.serverId)
        : await aiApi.listMemory('team', undefined, scope.teamId)
    return { rows, gesamt: rows.length, loeschbar: rows.length, seitengroesse: 0 }
  }

  const uebernehmen = (ladung: Ladung, zielSeite: number) => {
    setEntries(ladung.rows)
    setGesamt(ladung.gesamt)
    setLoeschbar(ladung.loeschbar)
    setSeitengroesse(ladung.seitengroesse)
    setSeite(zielSeite)
  }

  const laden = async (zielSeite = seite, groesse = seitengroesse) => {
    const ladung = await holen(Math.max(0, zielSeite - 1) * groesse)
    const letzte = ladung.seitengroesse > 0
      ? Math.max(1, Math.ceil(ladung.gesamt / ladung.seitengroesse))
      : 1
    // Wer die letzte Zeile der letzten Seite löscht, stünde sonst vor „Seite 25
    // von 24" und einer leeren Liste, die aussieht wie ein leeres Gedächtnis.
    // Statt dessen rutscht die Ansicht auf die letzte Seite, die es noch gibt.
    // Nur ein Nachschlag, denn `letzte` ist danach gültig.
    if (zielSeite > letzte) return laden(letzte, ladung.seitengroesse)
    uebernehmen(ladung, zielSeite)
  }

  useEffect(() => {
    if (!allowed) return
    let active = true
    setSuche(''); setHerkunft('all'); setKey(''); setValue(''); setBearbeitet(null)
    Promise.all([
      holen(0),
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
        || entry.value.toLowerCase().includes(nadel))
      // Zuletzt genutzt zuerst: was die KI gerade heranzieht, ist das, was man
      // prüfen will. Nie genutzte Einträge stehen hinten, nach Schlüssel sortiert.
      //
      // Im persönlichen Bereich sortiert `personal_entries` schon genauso, und
      // dort ist es keine Anzeigefrage mehr, sondern die Schnittkante zwischen
      // den Seiten. Diese Sortierung hier ist deshalb ein Nachziehen, kein
      // Umsortieren — wer sie ändert, muss die dortige mitändern, sonst kommt
      // eine Seite in einer anderen Reihenfolge an, als sie geschnitten wurde.
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
    //
    // Seit die Ansicht blättert, kann sie das nicht mehr selbst zählen: sie
    // sieht 200 von 5.000. `loeschbar` kommt deshalb vom Server, der beides
    // kennt — den Bestand und die Kennung, über die gelöscht wird.
    if (!await confirm({
      title: t('ai.memory.clearTitle'),
      message: t('ai.memory.clearConfirm', { count: loeschbar }),
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
      // Zurück auf Seite 1: nach dem Abräumen der allgemeinen Einträge bleiben
      // höchstens die Servernotizen übrig, und die passen wieder auf eine Seite.
      await laden(1)
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
      // Die Suche liest, was geladen ist — und geladen ist bei mehreren Seiten
      // nur diese eine. Sie könnte es auch gar nicht anders: der Wert liegt
      // verschlüsselt in der Datenbank, eine Suche über den ganzen Bestand
      // hieße, alle 5.000 Zeilen zu öffnen, also genau das, wogegen die
      // Seitenweise gebaut ist. Statt so zu tun, als suchte sie überall, sagt
      // die Beschriftung, worin sie sucht.
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
          {darfAendern && entries.length > 0 && (
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void allesEntfernen()}>
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t('ai.memory.clearAll')}
            </Button>
          )}
        </>
      )}
      // Der Zähler zählt, was der Filter von der geladenen Liste übrig lässt —
      // und die ist bei mehreren Seiten nur ein Ausschnitt. „3 von 4 angezeigt"
      // neben „5.000 Einträge insgesamt" läse sich wie ein Widerspruch, also
      // sagt er dann dazu, worauf er sich bezieht.
      count={werkzeugleiste
        ? (seitenzahl > 1
          ? t('ai.memory.countPage', { shown: sichtbar.length, total: entries.length })
          : t('ai.memory.count', { shown: sichtbar.length, total: entries.length }))
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

      {/* Die Gesamtzahl steht links daneben und nicht im Zähler über der Liste:
          der zählt, was der Filter von dieser Seite übrig lässt, und beides in
          einem Satz läse sich wie ein Widerspruch. Hier gehört sie hin, weil sie
          erklärt, wozu die Knöpfe daneben da sind. */}
      <Pagination
        page={seite}
        pageCount={seitenzahl}
        label={t('ai.memory.total', { count: gesamt })}
        disabled={busy}
        onChange={blaettern}
      />

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
