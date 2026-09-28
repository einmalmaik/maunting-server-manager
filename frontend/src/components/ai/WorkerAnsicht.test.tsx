/**
 * Das Worker-Fenster und die Worker-Leiste.
 *
 * Die Zusagen aus docs/agentic-framework.md (Frontend-Zeile): einsehbar,
 * nicht beschreibbar, räumt sich auf. Hier stehen sie als Tests, weil jede
 * sich mit einer unauffälligen Zeile wieder verlieren liesse:
 *
 * 1. Das Fenster lädt über die **Kennung** — `kind=worker` ist mehrdeutig,
 *    und ein Laden über die Art griffe bei N Aufträgen den falschen.
 * 2. Es gibt kein Eingabefeld und keinen Abbruch-Knopf; die Fusszeile nennt
 *    den Steuerweg (im Gespräch, `worker_cancel` ruft das Gehirn). Auch die
 *    Vorschlagskarten sind hier nur zu sehen (seit 25.09.2026).
 * 3. Die Leiste zeigt, was das Backend als lebend meldet, führt über die
 *    UUID in das Fenster — und rendert ohne Aufträge gar nichts.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, attachAiRun, type AiActionProposal, type AiMessage, type AiWorkerInfo } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { WorkerAnsicht } from './WorkerAnsicht'
import { WorkerLeiste } from './WorkerLeiste'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...original,
    aiApi: {
      getWorkerConversation: vi.fn(),
      listWorkerActions: vi.fn(),
      getWorkerRun: vi.fn(),
      listWorkers: vi.fn(),
    },
    attachAiRun: vi.fn(),
  }
})

const KONVERSATION = {
  id: 'konv-worker-1',
  kind: 'worker' as const,
  title: 'Backups prüfen',
  created_at: '2026-08-18T04:00:00Z',
  updated_at: '2026-08-18T04:00:00Z',
  has_more: false,
}

const nachricht = (id: string, role: AiMessage['role'], content: string): AiMessage => ({
  id, role, content, reasoning: null, question: null, status: 'complete',
  provider_id: null, model: null, created_at: '2026-08-18T04:00:00Z',
})

function Standort() {
  const ort = useLocation()
  return <span data-testid="standort">{`${ort.pathname}${ort.search}`}</span>
}

describe('WorkerAnsicht', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.getWorkerConversation).mockReset().mockResolvedValue({
      ...KONVERSATION,
      messages: [
        nachricht('msg-1', 'user', 'Auftrag: Prüfe die Backups aller Server.'),
        nachricht('msg-2', 'assistant', 'Backup von Minecraft-01 ist in Ordnung.'),
      ],
    })
    vi.mocked(aiApi.listWorkerActions).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.getWorkerRun).mockReset().mockResolvedValue(null)
    vi.mocked(attachAiRun).mockReset().mockResolvedValue(undefined)
    useToastStore.setState({ toasts: [] })
  })

  it('liest über die Kennung und zeigt den Verlauf des Auftrags', async () => {
    render(
      <MemoryRouter>
        <WorkerAnsicht conversationId="konv-worker-1" />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(aiApi.getWorkerConversation).toHaveBeenCalledWith('konv-worker-1')
    })
    expect(aiApi.listWorkerActions).toHaveBeenCalledWith('konv-worker-1')
    expect(aiApi.getWorkerRun).toHaveBeenCalledWith('konv-worker-1')
    expect(await screen.findByText('Backup von Minecraft-01 ist in Ordnung.')).toBeInTheDocument()
    expect(screen.getByText('Backups prüfen')).toBeInTheDocument()
  })

  it('hat kein Eingabefeld und nennt stattdessen den Steuerweg', async () => {
    render(
      <MemoryRouter>
        <WorkerAnsicht conversationId="konv-worker-1" />
      </MemoryRouter>,
    )

    await screen.findByText('Backup von Minecraft-01 ist in Ordnung.')
    // Kein Tippen: eine Nachricht löste über `vorgaenger_abloesen` den
    // laufenden Auftrag ab — dieselbe Begründung wie im Guardian-Fenster.
    expect(document.querySelector('textarea')).toBeNull()
    expect(document.querySelector('input[type="text"]')).toBeNull()
    expect(screen.getByText(/Gesteuert wird im Gespräch/)).toBeInTheDocument()
  })

  it('zeigt eine wartende Karte ohne Knöpfe und sagt, wo bestätigt wird', async () => {
    // Betreiber, 25.09.2026: „Der Worker-Chat ist eigentlich nur zum
    // Nachschauen". Dieselbe Karte steht mit Knöpfen im Chat und in der
    // Sprachansicht.
    const karte: AiActionProposal = {
      id: '0b7e7a52-2f6e-4a39-9d4e-3c2d1f0a9b11',
      conversation_id: 'konv-worker-1',
      server_id: 3,
      tool_name: 'propose_backup',
      proposal_type: 'write',
      preview: {},
      expected_revision: null,
      requires_confirmation: true,
      autonomous: false,
      reason: null,
      expected_effect: null,
      status: 'proposed',
      task_id: null,
      error_code: null,
      run_id: 'lauf-1',
      created_at: '2026-08-18T04:00:01Z',
    }
    vi.mocked(aiApi.listWorkerActions).mockResolvedValue([karte])

    render(
      <MemoryRouter>
        <WorkerAnsicht conversationId="konv-worker-1" />
      </MemoryRouter>,
    )

    expect(await screen.findByText(i18n.t('ai.actions.tools.propose_backup'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.actions.nurAnsicht'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('ai.actions.execute') })).toBeNull()
    expect(screen.queryByRole('button', { name: i18n.t('ai.actions.reject') })).toBeNull()
  })

  it('sagt ruhig, wenn es diesen Auftrag nicht gibt', async () => {
    // Der echte Fehler und nicht ein `Error` mit passend gesetztem `name`: die
    // Ansicht unterscheidet die beiden Fälle über `instanceof`, und eine
    // Attrappe daneben prüfte den lauten Zweig, während der Name dieses Tests
    // das Gegenteil verspricht.
    vi.mocked(aiApi.getWorkerConversation).mockRejectedValue(
      new SanitizedApiError('nicht gefunden', { status: 404 }),
    )

    render(
      <MemoryRouter>
        <WorkerAnsicht conversationId="konv-fremd" />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Diesen Worker gibt es nicht')).toBeInTheDocument()
    // „Ruhig" ist die halbe Zusage und die einzige, die man nicht sieht: ein
    // fremdes Lesezeichen ist kein Störfall, und ein roter Toast dazu wäre
    // eine Falschmeldung.
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it('meldet einen echten Störfall trotzdem laut', async () => {
    // Das Gegenstück. Ein abgerissenes Netz oder ein Fehler im Bauteil ist
    // keine Aussage über diesen Auftrag — bliebe auch er still, stünde
    // „Diesen Worker gibt es nicht" da, obwohl es ihn gibt.
    vi.mocked(aiApi.getWorkerConversation).mockRejectedValue(new Error('Failed to fetch'))

    render(
      <MemoryRouter>
        <WorkerAnsicht conversationId="konv-worker-1" />
      </MemoryRouter>,
    )

    await screen.findByText('Diesen Worker gibt es nicht')
    expect(useToastStore.getState().toasts.map((eintrag) => eintrag.message))
      .toEqual([i18n.t('ai.chat.errors.load')])
  })
})

describe('WorkerLeiste', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('rendert ohne Aufträge nichts — der Chat sieht aus wie immer', async () => {
    vi.mocked(aiApi.listWorkers).mockReset().mockResolvedValue([])

    const { container } = render(
      <MemoryRouter>
        <WorkerLeiste />
      </MemoryRouter>,
    )

    await waitFor(() => expect(aiApi.listWorkers).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('zeigt je lebendem Auftrag eine Pille und führt über die Kennung hin', async () => {
    const eintraege: AiWorkerInfo[] = [
      {
        conversation_id: 'konv-worker-1', title: 'Backups prüfen',
        status: 'running', created_at: '2026-08-18T04:00:00Z',
      },
      {
        conversation_id: 'konv-worker-2', title: 'Kalender aufräumen',
        status: 'waiting_wake', created_at: '2026-08-18T05:00:00Z',
      },
    ]
    vi.mocked(aiApi.listWorkers).mockReset().mockResolvedValue(eintraege)

    render(
      <MemoryRouter initialEntries={['/ai']}>
        <WorkerLeiste />
        <Standort />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Backups prüfen')).toBeInTheDocument()
    expect(screen.getByText('Kalender aufräumen')).toBeInTheDocument()
    expect(screen.getByText(/schläft bis zum nächsten Wecken/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('Backups prüfen'))
    await waitFor(() => {
      expect(screen.getByTestId('standort'))
        .toHaveTextContent('/ai?ansicht=worker&id=konv-worker-1')
    })
  })
})
