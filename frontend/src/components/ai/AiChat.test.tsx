import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiAttachment, type AiSkill } from '@/api/ai'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiChat } from './AiChat'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...original,
    aiApi: {
      listProviders: vi.fn(),
      getConversation: vi.fn(),
      clearHistory: vi.fn(),
      listActions: vi.fn(),
      listAttachments: vi.fn(),
      listSkills: vi.fn(),
      uploadAttachment: vi.fn(),
      deleteAttachment: vi.fn(),
      runSkill: vi.fn(),
      listAutonomyGrants: vi.fn(),
      saveAutonomyGrant: vi.fn(),
    },
    streamAiMessage: vi.fn(),
  }
})

vi.mock('@/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/client')>()
  return { ...original, api: vi.fn() }
})

vi.mock('./AiActionProposalCard', () => ({ AiActionProposalCard: () => null }))

const CONVERSATION = {
  id: '00000000-0000-0000-0000-000000000301',
  title: 'KI-Assistent',
  created_at: '2026-08-01T12:00:00Z',
  updated_at: '2026-08-01T12:00:00Z',
}

const attachment: AiAttachment = {
  id: '00000000-0000-0000-0000-000000000303',
  conversation_id: CONVERSATION.id,
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

describe('AiChat', () => {
  beforeEach(async () => {
    Element.prototype.scrollIntoView = vi.fn()
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: {
        is_owner: false, role_id: null, role_name: null,
        global_keys: ['ai.chat.use', 'ai.attachments.use', 'ai.skills.use'],
        server_keys: {},
      },
      isLoading: false,
      error: null,
    })
    vi.mocked(client.api).mockReset().mockResolvedValue([{ id: 7, name: 'Minecraft-01' }])
    vi.mocked(aiApi.listProviders).mockReset().mockResolvedValue([
      { id: 1, name: 'Synthetic AI', default_model: 'test-model', requires_api_key: false, user_key_configured: false, operator_key_available: true, available: true },
    ])
    vi.mocked(aiApi.getConversation).mockReset().mockResolvedValue({ ...CONVERSATION, messages: [] })
    vi.mocked(aiApi.listActions).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.listAttachments).mockReset().mockResolvedValue([attachment])
    vi.mocked(aiApi.listSkills).mockReset().mockResolvedValue(skills)
    vi.mocked(aiApi.uploadAttachment).mockReset().mockResolvedValue(attachment)
    vi.mocked(aiApi.clearHistory).mockReset().mockResolvedValue(undefined)
    vi.mocked(aiApi.runSkill).mockReset().mockResolvedValue({
      skill_id: 'latest-skill', version: 2, read_results: [], proposals: [],
    })
  })

  it('offers exactly one conversation and no way to create another', async () => {
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    // Der Kern der Aenderung: kein "Neue Unterhaltung", keine Chatliste.
    expect(screen.queryByRole('button', { name: /neue unterhaltung/i })).not.toBeInTheDocument()
    expect(aiApi.getConversation).toHaveBeenCalledWith()
  })

  it('never offers an outdated skill version', async () => {
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    expect(screen.getByLabelText('Skill auswählen')).toHaveTextContent('Latest check')
    fireEvent.click(screen.getByLabelText('Skill auswählen'))
    expect(screen.queryByText('Old check')).not.toBeInTheDocument()
  })

  it('uploads through the attachment endpoint of the single conversation', async () => {
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    const file = new File(['synthetic content'], 'another-synthetic.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText('Sicheren Anhang hinzufügen'), { target: { files: [file] } })

    await waitFor(() => expect(aiApi.uploadAttachment).toHaveBeenCalledWith(file))
  })

  it('runs a skill against the explicitly selected server, not the conversation', async () => {
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Skill starten' }))

    // Der Serverbezug haengt seit dem Einzelchat am Aufruf, nicht am Gespraech.
    await waitFor(() => expect(aiApi.runSkill).toHaveBeenCalledWith('latest-skill', 7))
  })

  it('sends the reasoning switch state along with the message', async () => {
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockResolvedValue(undefined)
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByRole('switch', { name: 'Nachdenken' }))
    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Hallo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await waitFor(() => expect(streamAiMessage).toHaveBeenCalledWith(
      expect.objectContaining({ content: 'Hallo', reasoning: true }),
      expect.any(Function),
      expect.any(AbortSignal),
    ))
  })
})
