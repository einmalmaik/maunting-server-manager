import { useEffect, useMemo, useState } from 'react'
import { BookOpen, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiSkillSummary } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { teamsApi, type Team } from '@/api/teams'
import { toast } from '@/stores/toastStore'

import { AiKnowledgeShell } from './AiKnowledgeShell'

/**
 * Was der Assistent gerade kennt — vollständig und ohne Bedienelemente.
 *
 * Die Bereichsansichten unter Teams und in den Einstellungen zeigen bewusst nur
 * ihren eigenen Bereich; keine von ihnen beantwortet mehr die Frage, die man am
 * Chat stellt: *woher* weiß er das? Genau dafür ist diese Liste da, und deshalb
 * steht sie dort und nirgends sonst.
 *
 * Nur lesend, und das ist der Punkt. Verwaltet wird, wo das Wissen hingehört —
 * ein zweites Formular hier hätte bedeutet, dass es drei Stellen mit demselben
 * Eingabefeld gibt und eine davon ohne sichtbaren Bereich.
 *
 * Die Teamnamen kommen aus `teamsApi.list()`, das der Benutzer ohnehin abrufen
 * darf. Ein eigener Endpunkt hätte dieselbe Auskunft ein zweites Mal gegeben.
 */
export function AiSkillDirectory() {
  const { t } = useTranslation()
  const [skills, setSkills] = useState<AiSkillSummary[]>([])
  const [teams, setTeams] = useState<Team[]>([])
  const [suche, setSuche] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    Promise.all([
      aiApi.listSkills(),
      // Ohne die Namen bleibt die Liste benutzbar — sie zeigt dann nur „Team"
      // statt „Team: Ops". Dafür soll sie nicht ganz ausfallen.
      teamsApi.list().catch(() => [] as Team[]),
    ])
      .then(([skillRows, teamRows]) => {
        if (!active) return
        setSkills(skillRows)
        setTeams(teamRows)
      })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.load'))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  const herkunft = useMemo(() => {
    const nachId = new Map(teams.map((team) => [team.id, team]))
    return (skill: AiSkillSummary): string => {
      if (skill.scope === 'shipped') return t('ai.skills.scopes.shipped')
      if (skill.team_id === null) return t('ai.skills.scopes.global')
      const team = nachId.get(skill.team_id)
      if (team === undefined) return t('ai.skills.scopes.team')
      if (team.is_personal) return t('ai.skills.scopes.personal')
      return t('ai.skills.scopes.namedTeam', { name: team.name })
    }
  }, [teams, t])

  const sichtbar = useMemo(() => {
    const nadel = suche.trim().toLowerCase()
    if (!nadel) return skills
    return skills.filter((skill) =>
      `${skill.name} ${skill.skill_key} ${skill.description}`.toLowerCase().includes(nadel))
  }, [skills, suche])

  if (loading) return null

  return (
    <AiKnowledgeShell
      icon={BookOpen}
      title={t('ai.skills.directoryTitle')}
      description={t('ai.skills.directoryDescription')}
      search={skills.length > 3 ? { value: suche, onChange: setSuche, label: t('ai.skills.search') } : undefined}
    >
      <ul className="space-y-2">
        {sichtbar.map((skill) => (
          <li
            key={skill.skill_key}
            className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-on-surface">{skill.name}</span>
              <span className="rounded-full border border-outline-variant/40 px-2 py-0.5 text-[10px] uppercase tracking-wider text-on-surface-variant">
                {herkunft(skill)}
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
        {sichtbar.length === 0 && (
          <li className="rounded-xl border border-dashed border-outline-variant/50 px-4 py-5 text-sm text-on-surface-variant">
            {skills.length === 0 ? t('ai.skills.empty') : t('ai.skills.noMatches')}
          </li>
        )}
      </ul>
      <p className="text-xs text-on-surface-variant">{t('ai.skills.directoryManageHint')}</p>
    </AiKnowledgeShell>
  )
}
