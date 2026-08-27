import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiActionProposal } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import i18n from '@/i18n'
import { useConfirmStore } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { AiActionProposalCard } from './AiActionProposalCard'

vi.mock('@/api/ai', () => ({
  aiApi: {
    confirmAction: vi.fn(),
    executeAction: vi.fn(),
    getAction: vi.fn(),
  },
}))
vi.mock('@/stores/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

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
  run_id: null,
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
    vi.mocked(aiApi.getAction).mockReset().mockResolvedValue(proposal)
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.error).mockReset()
  })

  it('executes a regular proposal directly upon clicking Ausführen without double modal', async () => {
    const onChange = vi.fn()
    render(<AiActionProposalCard proposal={proposal} onChange={onChange} />)

    expect(screen.getByText('server.cfg')).toBeInTheDocument()
    expect(screen.queryByText(/one-time-secret/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Jetzt ausführen' }))

    await waitFor(() => expect(aiApi.confirmAction).toHaveBeenCalledWith(proposal.id))
    await waitFor(() => expect(aiApi.executeAction).toHaveBeenCalledWith(
      proposal.id,
      'one-time-secret-token-value-123456789',
    ))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: 'succeeded' }))
    expect(screen.queryByText(/one-time-secret/)).not.toBeInTheDocument()
  })

  /**
   * Das Nein ist die eigentliche Zusage bei unumkehrbaren Aktionen.
   */
  it('leaves the API untouched when unrecoverable confirmation is declined', async () => {
    const destructiveProposal: AiActionProposal = {
      ...proposal,
      tool_name: 'propose_server_delete',
      preview: { operation: 'server_delete', path: '/servers/1' },
    }
    render(<AiActionProposalCard proposal={destructiveProposal} onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Jetzt ausführen' }))
    await act(async () => useConfirmStore.getState().resolve(false))

    expect(aiApi.confirmAction).not.toHaveBeenCalled()
    expect(aiApi.executeAction).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Jetzt ausführen' })).toBeEnabled()
  })

  it('never reaches execute when the confirmation call itself fails', async () => {
    vi.mocked(aiApi.confirmAction).mockRejectedValue(
      new SanitizedApiError('Die Freigabe ist abgelaufen.', { status: 410 }),
    )
    render(<AiActionProposalCard proposal={proposal} onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Jetzt ausführen' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Die Freigabe ist abgelaufen.'))
    expect(aiApi.executeAction).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('reports a failed execution and reloads the proposal instead of claiming success', async () => {
    vi.mocked(aiApi.executeAction).mockRejectedValue(
      new SanitizedApiError('Der Server ist gesperrt.', { status: 409 }),
    )
    vi.mocked(aiApi.getAction).mockResolvedValue({
      ...proposal, status: 'failed', error_code: 'server_locked',
    })
    const onChange = vi.fn()
    render(<AiActionProposalCard proposal={proposal} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Jetzt ausführen' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Der Server ist gesperrt.'))
    expect(toast.success).not.toHaveBeenCalled()
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'failed', error_code: 'server_locked' }),
    ))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Jetzt ausführen' })).toBeEnabled())
    expect(screen.queryByText(/one-time-secret/)).not.toBeInTheDocument()
  })

  it('shows the reasoning and expected effect required by the preview contract', () => {
    render(<AiActionProposalCard proposal={proposal} onChange={vi.fn()} />)

    expect(screen.getByText(/Der Port kollidiert/)).toBeInTheDocument()
    expect(screen.getByText(/ohne Portkonflikt/)).toBeInTheDocument()
  })

  it('names the tool and warns about untrusted provenance in the confirm dialog for destructive actions', () => {
    const destructiveProposal: AiActionProposal = {
      ...proposal,
      tool_name: 'propose_server_delete',
      preview: { operation: 'server_delete', path: '/servers/1' },
    }
    render(<AiActionProposalCard proposal={destructiveProposal} onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Jetzt ausführen' }))

    const message = useConfirmStore.getState().pending?.message ?? ''
    expect(message).toContain('Server löschen')
    expect(message).toMatch(/unvertrauenswürdiger Daten/i)
  })

  /**
   * Der API-Key einer frisch angelegten Shop-Anbindung.
   */
  it('shows a freshly created secret exactly once, from result and not from preview', async () => {
    const hosterProposal: AiActionProposal = {
      ...proposal,
      server_id: null,
      tool_name: 'propose_hoster_integration',
      preview: {
        operation: 'hoster_integration_create',
        path: 'mein-shop',
        slug: 'mein-shop',
        service_user: 'shop-dienst',
      },
    }
    vi.mocked(aiApi.executeAction).mockResolvedValue({
      proposal: { ...hosterProposal, status: 'succeeded' },
      result: { secrets: [{ label: 'API-Key', value: 'Zx9-KpQ2-einmalig-sichtbar' }] },
    })

    render(<AiActionProposalCard proposal={hosterProposal} onChange={vi.fn()} />)

    expect(screen.getByText('shop-dienst')).toBeInTheDocument()
    expect(screen.queryByText(/Zx9-KpQ2/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Jetzt ausführen' }))

    await waitFor(() => expect(screen.getByText(/Zx9-KpQ2/)).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'Verstanden' }))
    await waitFor(() => expect(screen.queryByText(/Zx9-KpQ2/)).not.toBeInTheDocument())
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
    expect(screen.queryByRole('button', { name: 'Jetzt ausführen' })).not.toBeInTheDocument()
  })

  it('renders read proposal type badge and preview without leaking file contents', () => {
    const readProposal: AiActionProposal = {
      ...proposal,
      id: 'proposal-read-1',
      tool_name: 'read_server_logs',
      proposal_type: 'read',
      preview: {
        server_name: 'Mein Server',
        zugriff: 'Logdatei einsehen',
        pfad: 'logs/latest.log',
        zeilen_angefragt: 50,
      },
    }

    render(<AiActionProposalCard proposal={readProposal} onChange={vi.fn()} />)

    expect(screen.getByText('Lese-Zugriff')).toBeInTheDocument()
    expect(screen.getByText('Mein Server')).toBeInTheDocument()
    expect(screen.getByText('Logdatei einsehen')).toBeInTheDocument()
    expect(screen.getByText('logs/latest.log')).toBeInTheDocument()
    expect(screen.getByText('50')).toBeInTheDocument()
  })

  it('renders worker proposal type badge and title preview', () => {
    const workerProposal: AiActionProposal = {
      ...proposal,
      id: 'proposal-worker-1',
      tool_name: 'worker_start',
      proposal_type: 'worker',
      preview: {
        titel: 'Recherche zu Backup-Fehler',
        beschreibung: 'Untersucht die Server-Fehlermeldungen im Hintergrund',
      },
    }

    render(<AiActionProposalCard proposal={workerProposal} onChange={vi.fn()} />)

    expect(screen.getByText('Worker')).toBeInTheDocument()
    expect(screen.getByText('Recherche zu Backup-Fehler')).toBeInTheDocument()
    expect(screen.getByText('Untersucht die Server-Fehlermeldungen im Hintergrund')).toBeInTheDocument()
  })

  it('renders desktop_artifact proposal with operation badge', () => {
    const artifactProposal: AiActionProposal = {
      ...proposal,
      id: 'proposal-art-1',
      tool_name: 'desktop_artifact',
      proposal_type: 'write',
      preview: {
        aktion: 'download',
        url: 'https://example.com/mod.zip',
        publisher_hash: 'abc12345',
      },
    }

    render(<AiActionProposalCard proposal={artifactProposal} onChange={vi.fn()} />)

    expect(screen.getByText('Desktop-Artefakt (Software/Mod/Installer)')).toBeInTheDocument()
    expect(screen.getByText('Aktion: download')).toBeInTheDocument()
  })
})
