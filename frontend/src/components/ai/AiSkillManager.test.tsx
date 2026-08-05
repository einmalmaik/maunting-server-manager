import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiSkill } from '@/api/ai'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiSkillManager } from './AiSkillManager'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...original,
    aiApi: {
      listManagedSkills: vi.fn(),
      createSkill: vi.fn(),
      updateSkill: vi.fn(),
    },
  }
})

const latest: AiSkill = {
  id: '00000000-0000-0000-0000-000000000202',
  skill_key: 'safe.status',
  version: 2,
  name: 'Safe status',
  description: 'Read status and propose a backup',
  steps: [
    { tool_name: 'read_server_status', arguments: {} },
    { tool_name: 'propose_backup', arguments: {} },
  ],
  enabled: true,
  created_by: 1,
  created_at: '2026-08-01T12:00:00Z',
}

describe('AiSkillManager', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: ['ai.skills.manage'], server_keys: {} },
      isLoading: false,
      error: null,
    })
    const old = { ...latest, id: 'old-version', version: 1, name: 'Old status' }
    vi.mocked(aiApi.listManagedSkills).mockReset().mockResolvedValue([old, latest])
    vi.mocked(aiApi.updateSkill).mockReset().mockResolvedValue({ ...latest, version: 3, enabled: false })
  })

  it('offers only the latest version and saves edits as a new immutable version', async () => {
    render(<AiSkillManager />)

    expect(await screen.findByDisplayValue('Safe status')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Old status')).not.toBeInTheDocument()
    expect(screen.getByText(/Schreibende Schritte erstellen nur einen Vorschlag/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Safe status revised' } })
    fireEvent.click(screen.getByRole('switch', { name: 'Skill aktiviert' }))
    fireEvent.click(screen.getByRole('button', { name: 'Neue Version speichern' }))

    await waitFor(() => expect(aiApi.updateSkill).toHaveBeenCalledWith('safe.status', expect.objectContaining({
      skill_key: 'safe.status',
      name: 'Safe status revised',
      enabled: false,
      steps: latest.steps,
    })))
  })

  it('does not load management data without permission', () => {
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: [], server_keys: {} },
      isLoading: false,
      error: null,
    })
    const { container } = render(<AiSkillManager />)
    expect(container).toBeEmptyDOMElement()
    expect(aiApi.listManagedSkills).not.toHaveBeenCalled()
  })
})
