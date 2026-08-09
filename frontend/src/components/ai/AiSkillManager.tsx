import { useEffect, useMemo, useState } from 'react'
import { BookOpen, Plus, Save, Sparkles, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiSkillManaged, type AiSkillSummary } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { teamsApi, type Team } from '@/api/teams'
import { Button, Dropdown, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

const EMPTY_DRAFT = { skill_key: '', name: '', description: '', body: '', team_id: null as number | null }

/**
 * Skills einsehen und pflegen.
 *
 * Zwei Listen, weil sie zwei verschiedene Fragen beantworten: **Verzeichnis**
 * zeigt, was die KI gerade kennt — einschließlich der mitgelieferten Vorgaben,
 * die niemand ändern kann. **Eigene** zeigt, was man selbst bearbeiten darf.
 *
 * Ein mitgelieferter Skill lässt sich ersetzen, indem man einen panelweiten mit
 * demselben Schlüssel anlegt. Das ist bewusst kein Bearbeiten: die Datei kommt
 * mit dem nächsten Update erneut, und eine überschriebene Datei wäre beim
 * nächsten Mal wieder da.
 */
export function AiSkillManager() {
  const { t } = useTranslation()
  const [index, setIndex] = useState<AiSkillSummary[]>([])
  const [managed, setManaged] = useState<AiSkillManaged[]>([])
  const [teams, setTeams] = useState<Team[]>([])
  const [draft, setDraft] = useState({ ...EMPTY_DRAFT })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const reload = async () => {
    const [indexRows, managedRows] = await Promise.all([
      aiApi.listSkills(),
      aiApi.listManagedSkills().catch(() => [] as AiSkillManaged[]),
    ])
    setIndex(indexRows)
    setManaged(managedRows)
  }

  useEffect(() => {
    let active = true
    Promise.all([
      aiApi.listSkills(),
      aiApi.listManagedSkills().catch(() => [] as AiSkillManaged[]),
      teamsApi.list().catch(() => [] as Team[]),
    ])
      .then(([indexRows, managedRows, teamRows]) => {
        if (!active) return
        setIndex(indexRows)
        setManaged(managedRows)
        setTeams(teamRows.filter((team) => team.can_manage_skills))
      })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.load'))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  // Panelweit darf nur, wer eine Zeile ohne Team verwalten darf. Statt das
  // Recht erneut abzufragen, leiten wir es aus dem ab, was das Backend
  // ohnehin geliefert hat — eine Wahrheit statt zweier.
  const canWriteGlobal = useMemo(
    () => managed.some((row) => row.team_id === null) || teams.length === 0,
    [managed, teams.length],
  )

  const scopeOptions = useMemo(() => [
    ...(canWriteGlobal ? [{ value: 'global', label: t('ai.skills.scopes.global') }] : []),
    ...teams.map((team) => ({ value: String(team.id), label: team.name })),
  ], [canWriteGlobal, t, teams])

  const save = async () => {
    if (busy) return
    setBusy(true)
    try {
      await aiApi.saveSkill({
        skill_key: draft.skill_key.trim().toLowerCase(),
        name: draft.name.trim(),
        description: draft.description.trim(),
        body: draft.body.trim(),
        team_id: draft.team_id,
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

  const remove = async (row: AiSkillManaged) => {
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

  const toggle = async (row: AiSkillManaged, enabled: boolean) => {
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

  return (
    <div className="space-y-5">
      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.skills.description')}</p>

      {/* ── Was die KI gerade kennt ─────────────────────────────────── */}
      <section aria-labelledby="skill-index">
        <h4 id="skill-index" className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
          {t('ai.skills.title')}
        </h4>
        <ul className="space-y-2">
          {index.map((skill) => (
            <li
              key={skill.skill_key}
              className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-on-surface">{skill.name}</span>
                <span className="rounded-full border border-outline-variant/40 px-2 py-0.5 text-[10px] uppercase tracking-wider text-on-surface-variant">
                  {t(`ai.skills.scopes.${skill.scope}`)}
                </span>
                {skill.origin === 'ai' && (
                  <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-tertiary">
                    <Sparkles className="h-3 w-3" aria-hidden="true" />
                    {t('ai.skills.origins.ai')}
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs leading-5 text-on-surface-variant">{skill.description}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* ── Eigene Skills ───────────────────────────────────────────── */}
      {(managed.length > 0 || scopeOptions.length > 0) && (
        <section aria-labelledby="skill-manage" className="space-y-3">
          <h4 id="skill-manage" className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('ai.skills.add')}
          </h4>

          {managed.length === 0 && <p className="text-sm text-on-surface-variant">{t('ai.skills.empty')}</p>}

          <ul className="space-y-2">
            {managed.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-center gap-3 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-3"
              >
                <button
                  type="button"
                  className="min-w-[10rem] flex-1 text-left"
                  onClick={() => {
                    setDraft({
                      skill_key: row.skill_key, name: row.name, description: row.description,
                      body: row.body, team_id: row.team_id,
                    })
                    setEditingId(row.id)
                  }}
                >
                  <span className="block text-sm font-medium text-on-surface">{row.name}</span>
                  <span className="block text-xs text-on-surface-variant">
                    {row.skill_key} · {t(`ai.skills.origins.${row.origin}`)}
                    {row.status === 'pending' && ` · ${t('ai.skills.pending')}`}
                  </span>
                </button>
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
              </li>
            ))}
          </ul>

          {scopeOptions.length > 0 && (
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
                <label className="min-w-[9rem] space-y-1.5">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('ai.skills.scope')}
                  </span>
                  <Dropdown
                    value={draft.team_id === null ? 'global' : String(draft.team_id)}
                    onChange={(value) => setDraft({ ...draft, team_id: value === 'global' ? null : Number(value) })}
                    options={scopeOptions}
                    disabled={busy}
                    aria-label={t('ai.skills.scope')}
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
                  <Save className="h-4 w-4" aria-hidden="true" />
                  {t('ai.skills.save')}
                </Button>
                {editingId !== null && (
                  <Button
                    type="button" variant="secondary" disabled={busy}
                    onClick={() => { setDraft({ ...EMPTY_DRAFT }); setEditingId(null) }}
                  >
                    <Plus className="h-4 w-4" aria-hidden="true" />
                    {t('ai.skills.add')}
                  </Button>
                )}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
