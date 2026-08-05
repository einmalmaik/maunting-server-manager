import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiAttachment, type AiConversation, type AiSkill } from '@/api/ai'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiChat } from './AiChat'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...original,
    aiApi: {
      listProviders: vi.fn(),
      listConversations: vi.fn(),
      getConversation: vi.fn(),
      listActions: vi.fn(),
      listAttachments: vi.fn(),
      listSkills: vi.fn(),
      uploadAttachment: vi.fn(),
      deleteAttachment: vi.fn(),
      runSkill: vi.fn(),
      createConversation: vi.fn(),
      deleteConversation: vi.fn(),
    },
  }
})

vi.mock('./AiActionProposalCard', () => ({ AiActionProposalCard: () => null }))

const conversations: AiConversation[] = [
  { id: '00000000-0000-0000-0000-000000000301', server_id: 7, title: 'First synthetic chat', created_at: '2026-08-01T12:00:00Z', updated_at: '2026-08-01T12:00:00Z' },
  { id: '00000000-0000-0000-0000-000000000302', server_id: 7, title: 'Second synthetic chat', created_at: '2026-08-01T12:00:00Z', updated_at: '2026-08-01T12:00:00Z' },
]

const attachment: AiAttachment = {
  id: '00000000-0000-0000-0000-000000000303',
  conversation_id: conversations[0].id,
  original_name: 'synthetic-note.txt',
  media_type: 'text/plain',
  size_bytes: 24,
  status: 'ready',
  rejection_code: null,
  created_at: '2026-08-01T12:00:00Z',
}

const skills: AiSkill[] = [
  { id: 'old-skill', skill_key: 'safe.check', version: 1, name: 'Old check', description: 'Old synthetic version', steps: [], enabled: true, created_by: 1, created_at: '2026-08-01T12:00:00Z' },
  { id: 'latest-skill', skill_key: 'safe.check', version: 2, name: 'Latest check', description: 'Latest synthetic version', steps: [{ tool_name: 'read_server_status', arguments: {} }], enabled: true, created_by: 1, created_at: '2026-08-01T12:00:00Z' },
]

describe('AiChat Phase 5 controls', () => {
  beforeEach(async () => {
    Element.prototype.scrollIntoView = vi.fn()
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: ['ai.chat.use', 'ai.attachments.use', 'ai.skills.use'], server_keys: {} },
      isLoading: false,
      error: null,
    })
    vi.mocked(aiApi.listProviders).mockReset().mockResolvedValue([{ id: 1, name: 'Synthetic AI', default_model: 'test-model', requires_api_key: false, user_key_configured: false, operator_key_available: true, available: true }])
    vi.mocked(aiApi.listConversations).mockReset().mockResolvedValue(conversations)
    vi.mocked(aiApi.getConversation).mockReset().mockImplementation(async (id) => ({ ...conversations.find((item) => item.id === id)!, messages: [] }))
    vi.mocked(aiApi.listActions).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.listAttachments).mockReset().mockImplementation(async (id) => id === conversations[0].id ? [attachment] : [])
    vi.mocked(aiApi.listSkills).mockReset().mockResolvedValue(skills)
    vi.mocked(aiApi.uploadAttachment).mockReset().mockResolvedValue(attachment)
    vi.mocked(aiApi.runSkill).mockReset().mockResolvedValue({ skill_id: 'latest-skill', version: 2, read_results: [{ tool_name: 'read_server_status', result: { status: 'stopped' } }], proposals: [] })
  })

  it('clears old attachments on conversation change and never offers an old skill version', async () => {
    render(<AiChat serverId={7} />)

    expect(await screen.findByText('synthetic-note.txt')).toBeInTheDocument()
    expect(screen.getByLabelText('Skill auswählen')).toHaveTextContent('Latest check')
    fireEvent.click(screen.getByLabelText('Skill auswählen'))
    expect(screen.queryByText('Old check')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Second synthetic chat' }))
    await waitFor(() => expect(screen.queryByText('synthetic-note.txt')).not.toBeInTheDocument())
    expect(aiApi.listAttachments).toHaveBeenCalledWith(conversations[1].id)
  })

  it('uploads only through the attachment endpoint and renders safe skill read results', async () => {
    render(<AiChat serverId={7} />)
    await screen.findByText('synthetic-note.txt')

    const file = new File(['synthetic content'], 'another-synthetic.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText('Sicheren Anhang hinzufügen'), { target: { files: [file] } })
    await waitFor(() => expect(aiApi.uploadAttachment).toHaveBeenCalledWith(conversations[0].id, file))

    fireEvent.click(screen.getByRole('button', { name: 'Skill starten' }))
    expect(await screen.findByText(/Sicheres Leseergebnis/)).toBeInTheDocument()
    expect(screen.getByText(/"status": "stopped"/)).toBeInTheDocument()
    expect(aiApi.runSkill).toHaveBeenCalledWith('latest-skill', conversations[0].id)
  })
})
