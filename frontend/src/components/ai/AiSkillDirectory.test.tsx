import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiSkillSummary } from '@/api/ai'
import { teamsApi, type Team } from '@/api/teams'
import i18n from '@/i18n'
import { AiSkillDirectory } from './AiSkillDirectory'

vi.mock('@/api/ai', () => ({ aiApi: { listSkills: vi.fn() } }))
vi.mock('@/api/teams', () => ({ teamsApi: { list: vi.fn() } }))
vi.mock('@/stores/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const basis = {
  id: null as string | null, status: 'active' as const, enabled: true, editable: false,
}

const skills: AiSkillSummary[] = [
  { ...basis, skill_key: 'dayz-start', name: 'DayZ-Start', description: 'a', scope: 'shipped', origin: 'shipped', team_id: null },
  { ...basis, id: '1', skill_key: 'backup', name: 'Backup-Routine', description: 'b', scope: 'global', origin: 'operator', team_id: null },
  { ...basis, id: '2', skill_key: 'meins', name: 'Meine Art', description: 'c', scope: 'team', origin: 'ai', team_id: 1 },
  { ...basis, id: '3', skill_key: 'freitags', name: 'Freitags', description: 'd', scope: 'team', origin: 'operator', team_id: 2 },
]

const teams: Team[] = [
  { id: 1, name: 'einmalmaik', is_personal: true, owner_user_id: 1, is_owner: true, can_manage_skills: true, can_manage_memory: true, member_count: 1, created_at: '2026-08-09T10:00:00Z' },
  { id: 2, name: 'Ops', is_personal: false, owner_user_id: 1, is_owner: true, can_manage_skills: true, can_manage_memory: true, member_count: 3, created_at: '2026-08-09T10:00:00Z' },
]

/**
 * Die eine Stelle, die „was kennt der Assistent gerade?" beantwortet.
 *
 * Die Bereichsansichten zeigen bewusst nur ihren eigenen Bereich — sonst stünde
 * dieselbe Liste an vier Stellen und keine davon wäre vollständig. Diese hier
 * ist vollständig und dafür ohne jedes Bedienelement: verwaltet wird, wo das
 * Wissen hingehört.
 */
describe('AiSkillDirectory', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.listSkills).mockReset().mockResolvedValue(skills)
    vi.mocked(teamsApi.list).mockReset().mockResolvedValue(teams)
  })

  it('benennt jede Herkunft, und das persönliche Team heißt nicht nach dem Benutzer', async () => {
    render(<AiSkillDirectory />)
    await screen.findByText('DayZ-Start')

    expect(screen.getByText('mitgeliefert')).toBeInTheDocument()
    expect(screen.getByText('panelweit')).toBeInTheDocument()
    expect(screen.getByText('persönlich')).toBeInTheDocument()
    expect(screen.getByText('Team: Ops')).toBeInTheDocument()
    // Das Ein-Mann-Team trägt den Benutzernamen. Ihn hier zu zeigen hieße, dem
    // Benutzer sein eigenes Wissen als „Team" zu verkaufen.
    expect(screen.queryByText('Team: einmalmaik')).not.toBeInTheDocument()
  })

  /**
   * Mitgelieferte Skills stehen in Dateien, nicht in der Tabelle — sie tauchen in
   * keiner Verwaltungsansicht auf. Der einzige dokumentierte Weg, einen davon zu
   * ersetzen, führt über einen panelweiten Skill mit demselben Schlüssel. Ohne
   * den Schlüssel und ohne den Satz, der den Weg nennt, müsste der Betreiber
   * beides raten.
   */
  it('nennt bei mitgelieferten Skills den Schlüssel und den Weg, sie zu ersetzen', async () => {
    render(<AiSkillDirectory />)
    await screen.findByText('DayZ-Start')

    expect(screen.getByText('dayz-start')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.skills.readOnly'))).toBeInTheDocument()
  })

  it('hängt den Ersetzungshinweis nur an mitgelieferte Skills', async () => {
    render(<AiSkillDirectory />)
    await screen.findByText('DayZ-Start')

    // Ein eigener Skill ist im Panel änderbar — der Hinweis wäre dort falsch.
    expect(screen.getAllByText(i18n.t('ai.skills.readOnly'))).toHaveLength(1)
    expect(screen.getByText('backup')).toBeInTheDocument()
  })

  it('bietet nichts zum Ändern an', async () => {
    render(<AiSkillDirectory />)
    await screen.findByText('DayZ-Start')

    expect(screen.queryByLabelText(/^Löschen:/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Skill-Schlüssel')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Hinzufügen' })).not.toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })
})
