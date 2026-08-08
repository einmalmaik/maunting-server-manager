import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiActionProposal } from '@/api/ai'
import i18n from '@/i18n'
import { useConfirmStore } from '@/stores/confirmStore'
import { AiActionProposalCard } from './AiActionProposalCard'

vi.mock('@/api/ai', () => ({
  aiApi: {
    confirmAction: vi.fn(),
    executeAction: vi.fn(),
    getAction: vi.fn(),
  },
}))

const proposal: AiActionProposal = {
  id: 'proposal-1',
  conversation_id: 'conversation-1',
  server_id: 2,
  tool_name: 'propose_config_update',
  preview: { path: 'server.cfg', diff: '-port=2302\n+port=2402' },
  expected_revision: 'sha256:abc',
  requires_confirmation: true,
  autonomous: false,
  reason: 'Der Port kollidiert mit einem anderen Server.',
  expected_effect: 'Nach dem Neustart startet der Server ohne Portkonflikt.',
  status: 'proposed',
  task_id: null,
  error_code: null,
  created_at: '2026-08-01T12:00:00Z',
}

describe('AiActionProposalCard', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    useConfirmStore.setState({ pending: null })
    vi.mocked(aiApi.confirmAction).mockReset().mockResolvedValue({
      proposal_id: proposal.id,
      confirmation_token: 'one-time-secret-token-value-123456789',
      expires_at: '2026-08-01T12:05:00Z',
    })
    vi.mocked(aiApi.executeAction).mockReset().mockResolvedValue({
      proposal: { ...proposal, status: 'succeeded' },
      result: {},
    })
  })

  it('requires explicit confirmation and passes the one-time token only to execute', async () => {
    const onChange = vi.fn()
    render(<AiActionProposalCard proposal={proposal} onChange={onChange} />)

    expect(screen.getByText('server.cfg')).toBeInTheDocument()
    expect(screen.queryByText(/one-time-secret/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Prüfen und ausführen' }))
    expect(aiApi.confirmAction).not.toHaveBeenCalled()

    await act(async () => useConfirmStore.getState().resolve(true))

    await waitFor(() => expect(aiApi.confirmAction).toHaveBeenCalledWith(proposal.id))
    await waitFor(() => expect(aiApi.executeAction).toHaveBeenCalledWith(
      proposal.id,
      'one-time-secret-token-value-123456789',
    ))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: 'succeeded' }))
    expect(screen.queryByText(/one-time-secret/)).not.toBeInTheDocument()
  })

  it('shows the reasoning and expected effect required by the preview contract', () => {
    render(<AiActionProposalCard proposal={proposal} onChange={vi.fn()} />)

    expect(screen.getByText(/Der Port kollidiert/)).toBeInTheDocument()
    expect(screen.getByText(/ohne Portkonflikt/)).toBeInTheDocument()
  })

  it('names the tool and warns about untrusted provenance in the confirm dialog', () => {
    render(<AiActionProposalCard proposal={proposal} onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Prüfen und ausführen' }))

    const message = useConfirmStore.getState().pending?.message ?? ''
    expect(message).toContain('Konfiguration ändern')
    expect(message).toContain('server.cfg')
    expect(message).toMatch(/unvertrauenswürdiger Daten/i)
  })

  it('renders an executed autonomous action as a report without an action button', () => {
    render(
      <AiActionProposalCard
        proposal={{ ...proposal, autonomous: true, requires_confirmation: false, status: 'succeeded' }}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getByText('Automatisch ausgeführt')).toBeInTheDocument()
    // Eine bereits gelaufene Aktion darf keinen Ausfuehren-Knopf anbieten.
    expect(screen.queryByRole('button', { name: 'Prüfen und ausführen' })).not.toBeInTheDocument()
  })
})
