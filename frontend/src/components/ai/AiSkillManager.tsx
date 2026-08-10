import { useEffect, useMemo, useState } from 'react'
import { BookOpen, Plus, Save, Sparkles, Trash2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiSkillManaged, type AiSkillSummary } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

import { AiKnowledgeShell } from './AiKnowledgeShell'
import { type AiSkillScope, skillScopeTeamId } from './knowledgeScope'

const EMPTY_DRAFT = { skill_key: '', name: '', description: '', body: '' }

interface Props {
  /** Wessen Skills — panelweit oder die eines bestimmten Teams. */
  scope: AiSkillScope
}

/**
 * Eine Zeile der Liste, unabhängig davon, woher sie stammt.
 *
 * Die beiden Quellen beantworten verschiedene Fragen: `manage` liefert alles,
 * was in diesem Bereich steht — auch Abgeschaltetes und noch nicht Freigegebenes
 * —, `skills` nur das, was gerade wirkt. Wer verwalten darf, braucht das erste;
 * wer nur mitliest, das zweite. Deshalb genau eine Liste mit zwei Herkünften
 * statt zweier Listen nebeneinander.
 */
interface Zeile {
  id: string | null
  skill_key: string
  name: string
  description: string
  body: string
  origin: 'shipped' | 'operator' | 'ai'
  status: 'active' | 'pending'
  enabled: boolean
}

function ausVerwaltet(row: AiSkillManaged): Zeile {
  return {
    id: row.id, skill_key: row.skill_key, name: row.name, description: row.description,
    body: row.body, origin: row.origin, status: row.status, enabled: row.enabled,
  }
}

function ausVerzeichnis(row: AiSkillSummary): Zeile {
  return {
    id: row.id, skill_key: row.skill_key, name: row.name, description: row.description,
    body: '', origin: row.origin, status: row.status, enabled: row.enabled,
  }
}

/**
 * Die Skills **eines** Bereichs einsehen und pflegen.
 *
 * Der Bereich steht immer fest — panelweit unter Einstellungen, das persönliche
 * Team oder ein beigetretenes unter Teams. Vorher gab es zusätzlich einen
 * bereichslosen Modus, der alles zusammen zeigte und ein Bereichs-Dropdown ins
 * Formular hängte; er stand im Profil und unter „Persönlich" und war an beiden
 * Stellen dieselbe Ansicht. Die Frage „was kennt der Assistent insgesamt?"
 * beantwortet jetzt {@link AiSkillDirectory} an einer Stelle.
 *
 * Die Hülle ist dieselbe wie bei den Erinnerungen ({@link AiKnowledgeShell}),
 * damit beide Bereiche sich gleich bedienen lassen.
 */
export function AiSkillManager({ scope }: Props) {
  const { t } = useTranslation()
  const darfAendern = scope.canManage
  const teamId = skillScopeTeamId(scope)

  const [zeilen, setZeilen] = useState<Zeile[]>([])
  const [suche, setSuche] = useState('')
  const [draft, setDraft] = useState({ ...EMPTY_DRAFT })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const reload = async () => {
    // Wer verwalten darf, sieht auch Abgeschaltetes und Wartendes — das
    // Verzeichnis blendet beides aus und wäre für eine Verwaltung blind.
    if (darfAendern) {
      const rows = await aiApi.listManagedSkills()
      setZeilen(rows.filter((row) => row.team_id === teamId).map(ausVerwaltet))
      return
    }
    const rows = await aiApi.listSkills()
    setZeilen(
      rows
        .filter((row) => row.scope !== 'shipped' && row.team_id === teamId)
        .map(ausVerzeichnis),
    )
  }

  useEffect(() => {
    let active = true
    setSuche('')
    setDraft({ ...EMPTY_DRAFT })
    setEditingId(null)
    setLoading(true)
    reload()
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.load'))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [darfAendern, teamId, t])

  const sichtbar = useMemo(() => {
    const nadel = suche.trim().toLowerCase()
    if (!nadel) return zeilen
    return zeilen.filter((row) =>
      `${row.name} ${row.skill_key} ${row.description}`.toLowerCase().includes(nadel))
  }, [zeilen, suche])

  const save = async () => {
    if (busy) return
    setBusy(true)
    try {
      await aiApi.saveSkill({
        skill_key: draft.skill_key.trim().toLowerCase(),
        name: draft.name.trim(),
        description: draft.description.trim(),
        body: draft.body.trim(),
        team_id: teamId,
        enabled: true,
      })
      setDraft({ ...EMPTY_DRAFT })
      setEditingId(null)
      await reload()
      toast.success(t('ai.skills.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (row: Zeile) => {
    if (row.id === null) return
    if (!await confirm({ message: t('ai.skills.remove'), confirmText: t('common.delete'), danger: true })) return
    setBusy(true)
    try {
      await aiApi.deleteSkill(row.id)
      if (editingId === row.id) {
        setDraft({ ...EMPTY_DRAFT })
        setEditingId(null)
      }
      await reload()
      toast.success(t('ai.skills.deleted'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (row: Zeile, enabled: boolean) => {
    if (row.id === null) return
    setBusy(true)
    try {
      await aiApi.toggleSkill(row.id, enabled)
      await reload()
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return null

  const valid = draft.skill_key.trim().length >= 2 && draft.name.trim() && draft.description.trim() && draft.body.trim()
  const beschreibung = scope.kind === 'panel'
    ? t('ai.skills.panelDescription')
    : scope.personal ? t('ai.skills.personalDescription') : t('ai.skills.teamDescription')

  return (
    <AiKnowledgeShell
      icon={BookOpen}
      title={t('ai.skills.title')}
      description={beschreibung}
      search={zeilen.length > 3 ? { value: suche, onChange: setSuche, label: t('ai.skills.search') } : undefined}
    >
      <ul className="space-y-2">
        {sichtbar.map((row) => (
          <li
            key={row.skill_key}
            className={`flex flex-wrap items-center gap-3 rounded-xl border p-3 ${
              editingId !== null && editingId === row.id
                ? 'border-primary/50 bg-primary/5'
                : 'border-outline-variant/40 bg-surface-container-low/35'
            }`}
          >
            <div className="min-w-[10rem] flex-1">
              {darfAendern ? (
                <button
                  type="button"
                  className="w-full text-left"
                  onClick={() => {
                    setDraft({
                      skill_key: row.skill_key, name: row.name,
                      description: row.description, body: row.body,
                    })
                    setEditingId(row.id)
                  }}
                >
                  <span className="block text-sm font-medium text-on-surface">{row.name}</span>
                </button>
              ) : (
                <span className="block text-sm font-medium text-on-surface">{row.name}</span>
              )}
              <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
                <span>{row.skill_key}</span>
                {row.origin === 'ai' && (
                  <span className="inline-flex items-center gap-1 uppercase tracking-wider text-tertiary">
                    <Sparkles className="h-3 w-3" aria-hidden="true" />
                    {t('ai.skills.origins.ai')}
                  </span>
                )}
                {row.status === 'pending' && <span>{t('ai.skills.pending')}</span>}
              </span>
              <p className="mt-1 text-xs leading-5 text-on-surface-variant">{row.description}</p>
            </div>
            {darfAendern && (
              <>
                <Switch
                  checked={row.enabled}
                  disabled={busy}
                  onCheckedChange={(next) => void toggle(row, next)}
                  aria-label={`${t('ai.skills.enabled')}: ${row.name}`}
                />
                <Button
                  type="button" variant="ghost" size="sm" disabled={busy}
                  onClick={() => void remove(row)}
                  aria-label={`${t('common.delete')}: ${row.name}`}
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </Button>
              </>
            )}
          </li>
        ))}
        {sichtbar.length === 0 && (
          <li className="rounded-xl border border-dashed border-outline-variant/50 px-4 py-5 text-sm text-on-surface-variant">
            {zeilen.length === 0 ? t('ai.skills.empty') : t('ai.skills.noMatches')}
          </li>
        )}
      </ul>

      {darfAendern && (
        <div className="space-y-3 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
          <div className="flex flex-wrap gap-3">
            <label className="min-w-[10rem] flex-1 space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.skills.key')}
              </span>
              <input
                className="msm-input" maxLength={64} value={draft.skill_key} disabled={busy || editingId !== null}
                onChange={(event) => setDraft({ ...draft, skill_key: event.target.value })}
                placeholder="valheim-ram"
                aria-label={t('ai.skills.key')}
              />
            </label>
            <label className="min-w-[10rem] flex-1 space-y-1.5">
              <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('ai.skills.name')}
              </span>
              <input
                className="msm-input" maxLength={100} value={draft.name} disabled={busy}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                aria-label={t('ai.skills.name')}
              />
            </label>
          </div>

          <label className="block space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.skills.descriptionLabel')}
            </span>
            <input
              className="msm-input" maxLength={500} value={draft.description} disabled={busy}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              aria-label={t('ai.skills.descriptionLabel')}
            />
            <span className="block text-xs text-on-surface-variant">{t('ai.skills.descriptionHint')}</span>
          </label>

          <label className="block space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.skills.body')}
            </span>
            <textarea
              className="msm-input min-h-[10rem] font-mono text-xs"
              maxLength={12_000} value={draft.body} disabled={busy}
              onChange={(event) => setDraft({ ...draft, body: event.target.value })}
              aria-label={t('ai.skills.body')}
            />
            <span className="block text-xs text-on-surface-variant">{t('ai.skills.bodyHint')}</span>
          </label>

          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={busy || !valid} onClick={() => void save()}>
              {editingId === null
                ? <Plus className="h-4 w-4" aria-hidden="true" />
                : <Save className="h-4 w-4" aria-hidden="true" />}
              {editingId === null ? t('ai.skills.add') : t('ai.skills.save')}
            </Button>
            {editingId !== null && (
              <Button
                type="button" variant="secondary" disabled={busy}
                onClick={() => { setDraft({ ...EMPTY_DRAFT }); setEditingId(null) }}
              >
                <X className="h-4 w-4" aria-hidden="true" />
                {t('common.cancel')}
              </Button>
            )}
          </div>
        </div>
      )}
    </AiKnowledgeShell>
  )
}
