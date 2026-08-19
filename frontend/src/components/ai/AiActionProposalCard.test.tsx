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

  /**
   * Das Nein ist die eigentliche Zusage dieser Karte.
   *
   * Sie ist die Stelle, an der ein Mensch einen Modellvorschlag ausfuehrt —
   * Server loeschen, Backup ueberspielen, Datei entfernen. Alles davor ist
   * Anzeige; erst hier faellt die Entscheidung. Ein `if (!accepted) return`,
   * das niemand prueft, laesst sich beim naechsten Umbau streichen, ohne dass
   * eine Zeile rot wird.
   */
  it('leaves the API untouched when the confirmation is declined', async () => {
    render(<AiActionProposalCard proposal={proposal} onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Prüfen und ausführen' }))
    await act(async () => useConfirmStore.getState().resolve(false))

    expect(aiApi.confirmAction).not.toHaveBeenCalled()
    expect(aiApi.executeAction).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
    // Ein Nein ist kein laufender Vorgang: der Knopf bleibt bedienbar, sonst
    // waere die Karte nach einem Fehlklick tot.
    expect(screen.getByRole('button', { name: 'Prüfen und ausführen' })).toBeEnabled()
  })

  it('never reaches execute when the confirmation call itself fails', async () => {
    // Der Token ist die Autorisierung. Bricht sein Abruf ab — abgelaufener
    // Vorschlag, entzogenes Recht —, darf `executeAction` gar nicht erst
    // versucht werden; ein Ersatzwert an dieser Stelle waere ein Ausfuehren
    // ohne Freigabe.
    vi.mocked(aiApi.confirmAction).mockRejectedValue(
      new SanitizedApiError('Die Freigabe ist abgelaufen.', { status: 410 }),
    )
    render(<AiActionProposalCard proposal={proposal} onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Prüfen und ausführen' }))
    await act(async () => useConfirmStore.getState().resolve(true))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Die Freigabe ist abgelaufen.'))
    expect(aiApi.executeAction).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('reports a failed execution and reloads the proposal instead of claiming success', async () => {
    // Ohne das Nachladen bliebe die Karte auf „proposed" stehen und boete den
    // Knopf ein zweites Mal an — obwohl der Vorschlag im Backend laengst
    // verbraucht oder fehlgeschlagen ist.
    vi.mocked(aiApi.executeAction).mockRejectedValue(
      new SanitizedApiError('Der Server ist gesperrt.', { status: 409 }),
    )
    vi.mocked(aiApi.getAction).mockResolvedValue({
      ...proposal, status: 'failed', error_code: 'server_locked',
    })
    const onChange = vi.fn()
    render(<AiActionProposalCard proposal={proposal} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Prüfen und ausführen' }))
    await act(async () => useConfirmStore.getState().resolve(true))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Der Server ist gesperrt.'))
    expect(toast.success).not.toHaveBeenCalled()
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'failed', error_code: 'server_locked' }),
    ))
    // Kein halb ausgefuehrter Zustand: der Token ist verbraucht, die Karte ist
    // wieder bedienbar, und ein Geheimnis aus einem gescheiterten Lauf gibt es
    // nicht.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Prüfen und ausführen' })).toBeEnabled())
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

  /**
   * Der API-Key einer frisch angelegten Shop-Anbindung.
   *
   * Er entsteht erst beim Ausfuehren, geht ueber `result` an genau diese Karte
   * und wird nirgends gespeichert — nicht in `preview`, nicht im Verlauf, nicht
   * im Modellkontext. Das Backend sichert die eine Haelfte zu; hier steht die
   * andere: er erscheint einmal und ueberlebt kein Neuladen, weil er nur im
   * Zustand dieser Komponente lebt.
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

    // Vom Panel aufgeloeste Tatsache, nicht Modellprosa: der Dienstbenutzer
    // steht mit Namen da, bevor jemand bestaetigt.
    expect(screen.getByText('shop-dienst')).toBeInTheDocument()
    expect(screen.queryByText(/Zx9-KpQ2/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Prüfen und ausführen' }))
    await act(async () => useConfirmStore.getState().resolve(true))

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
    expect(screen.queryByRole('button', { name: 'Prüfen und ausführen' })).not.toBeInTheDocument()
  })
})
