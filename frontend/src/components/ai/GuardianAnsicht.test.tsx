/**
 * Das Guardian-Fenster.
 *
 * Es ist der sichtbare Teil davon, dass eine Reparatur ein Hintergrundprozess
 * ist: sie läuft, ob jemand zusieht oder nicht, und wer hinsieht, sieht ihr zu.
 * Die beiden Zusagen, an denen das hängt — es liest den *Guardian*-Verlauf,
 * nicht den Chat, und man kann darin nichts tippen — stehen hier als Tests,
 * weil beide sich mit einer einzigen unauffälligen Zeile wieder verlieren
 * ließen.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  aiApi,
  attachAiRun,
  type AiActionProposal,
  type AiMessage,
  type AiRunInfo,
} from '@/api/ai'
import i18n from '@/i18n'
import { GuardianAnsicht } from './GuardianAnsicht'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...original,
    aiApi: {
      getConversation: vi.fn(),
      listActions: vi.fn(),
      getActiveRun: vi.fn(),
      takeOverGuardian: vi.fn(),
    },
    attachAiRun: vi.fn(),
  }
})

// Dieselbe Attrappe wie im Chat: ein Knopf, der genau das auslöst, was die
// echte Karte nach dem Bestätigen tut. Ohne ihn liesse sich die Zusage
// „Vorschlagskarten bleiben bedienbar" nicht prüfen.
vi.mock('./AiActionProposalCard', () => ({
  AiActionProposalCard: ({
    proposal, onChange,
  }: { proposal: AiActionProposal; onChange: (p: AiActionProposal) => void }) => (
    <button type="button" onClick={() => onChange({ ...proposal, status: 'succeeded' })}>
      bestaetigen-attrappe
    </button>
  ),
}))

const KONVERSATION = {
  id: 'konv-guardian',
  kind: 'guardian' as const,
  title: 'Guardian-Reparaturen',
  created_at: '2026-08-16T04:00:00Z',
  updated_at: '2026-08-16T04:00:00Z',
  has_more: false,
}

const nachricht = (id: string, role: AiMessage['role'], content: string): AiMessage => ({
  id, role, content, reasoning: null, question: null, status: 'complete',
  provider_id: null, model: null, created_at: '2026-08-16T04:00:00Z',
})

const vorschlag: AiActionProposal = {
  id: 'vorschlag-1', conversation_id: KONVERSATION.id, server_id: 7,
  tool_name: 'propose_server_lifecycle', preview: {}, expected_revision: null,
  requires_confirmation: true, autonomous: false, reason: null,
  expected_effect: null, status: 'proposed', task_id: null, error_code: null,
  run_id: 'lauf-1', created_at: '2026-08-16T04:05:00Z',
}

const lauf = (over: Partial<AiRunInfo> = {}): AiRunInfo => ({
  id: 'lauf-1', status: 'running', stop_reason: null, message_id: 'msg-2',
  live: true, created_at: '2026-08-16T04:00:00Z',
  kind: 'guardian', conversation_id: KONVERSATION.id, server_id: 7,
  ...over,
})

function verlauf(messages: AiMessage[]) {
  return { ...KONVERSATION, messages }
}

/** Verrät, wo der Router steht — „Übernehmen" führt in den Chat. */
function Standort() {
  const ort = useLocation()
  return <span data-testid="standort">{`${ort.pathname}${ort.search}`}</span>
}

function zeichnen() {
  return render(
    <MemoryRouter initialEntries={['/ai?ansicht=guardian']}>
      <GuardianAnsicht />
      <Standort />
    </MemoryRouter>,
  )
}

describe('GuardianAnsicht', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.getConversation).mockReset().mockResolvedValue(verlauf([]))
    vi.mocked(aiApi.listActions).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.getActiveRun).mockReset().mockResolvedValue(null)
    vi.mocked(attachAiRun).mockReset().mockResolvedValue(undefined)
    vi.mocked(aiApi.takeOverGuardian).mockReset().mockResolvedValue({ aborted: 1 })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('liest den Guardian-Verlauf und nicht den Chat', async () => {
    // Die Vorgabe aller drei Aufrufe ist `'primary'`. Ohne das ausdrückliche
    // Argument zeigte dieses Fenster den Dauerchat — dieselben Zeilen, nur an
    // der falschen Stelle, und niemandem fiele es sofort auf.
    zeichnen()

    await waitFor(() => expect(aiApi.getConversation).toHaveBeenCalledWith('guardian'))
    expect(aiApi.listActions).toHaveBeenCalledWith('guardian')
    expect(aiApi.getActiveRun).toHaveBeenCalledWith('guardian')
  })

  it('zeigt den Auftragstext und die Antwort der KI', async () => {
    vi.mocked(aiApi.getConversation).mockResolvedValue(verlauf([
      nachricht('msg-1', 'user', 'Guardian meldet: Minecraft-01 startet nicht.'),
      nachricht('msg-2', 'assistant', 'Ich sehe mir die Logs an.'),
    ]))

    zeichnen()

    expect(await screen.findByText('Guardian meldet: Minecraft-01 startet nicht.'))
      .toBeInTheDocument()
    expect(screen.getByText('Ich sehe mir die Logs an.')).toBeInTheDocument()
  })

  it('hat kein Eingabefeld', async () => {
    // **Der Grund für das eigene Fenster.** Eine getippte Nachricht löst über
    // `vorgaenger_abloesen` jeden offenen Lauf ihrer Unterhaltung ab. Hier
    // liefe seit vier Uhr eine Reparatur — ein Eingabefeld wäre ein Knopf zum
    // versehentlichen Abbrechen.
    zeichnen()

    await waitFor(() => expect(aiApi.getConversation).toHaveBeenCalled())
    expect(screen.queryByRole('textbox')).toBeNull()
    // Und der Hinweis steht dort, wo sonst das Feld wäre — sonst sucht man es.
    expect(screen.getByText(i18n.t('ai.guardian.readOnly'))).toBeInTheDocument()
  })

  it('hängt sich an eine laufende Reparatur an', async () => {
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(lauf())

    zeichnen()

    await waitFor(() => expect(attachAiRun).toHaveBeenCalledTimes(1))
    expect(vi.mocked(attachAiRun).mock.calls[0][0]).toBe('lauf-1')
  })

  it('hängt sich an einen bereits fertigen Lauf nicht immer wieder an', async () => {
    // Der Zustand `lauf` bleibt hier stehen, anders als im Chat, wo er beim
    // Öffnen verbraucht wird. Ohne die Merkzeile hinge sich der Effekt in einer
    // Schleife an denselben, längst beendeten Lauf: Strom endet, `streaming`
    // fällt, `live` steht noch — und von vorn.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(lauf())

    zeichnen()

    await waitFor(() => expect(attachAiRun).toHaveBeenCalledTimes(1))
    // Genug Runden, damit eine Schleife sich zeigen würde.
    await new Promise((fertig) => setTimeout(fertig, 50))
    expect(attachAiRun).toHaveBeenCalledTimes(1)
  })

  it('versucht es bei einem Lauf ohne Strom gar nicht erst', async () => {
    // `live: false` heisst: die Zeile steht in der Datenbank, aber niemand
    // hält den Lauf im Speicher — nach einem Neustart des Panels der
    // Normalfall. Ein Anhängeversuch endete in einem Ladebalken, der sich nie
    // bewegt.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(lauf({ status: 'waiting_confirmation', live: false }))

    zeichnen()

    await waitFor(() => expect(aiApi.getActiveRun).toHaveBeenCalled())
    await new Promise((fertig) => setTimeout(fertig, 50))
    expect(attachAiRun).not.toHaveBeenCalled()
  })

  it('sagt, wenn die Reparatur auf eine E-Mail-Freigabe wartet', async () => {
    // Ein Reparaturlauf darf warten, seit ein bestätigungspflichtiger Schritt
    // per E-Mail hinausgeht statt den Lauf zu beenden. Ohne diesen Hinweis
    // sähe das Fenster genauso aus wie nach einem Abbruch — und der Betreiber
    // suchte den Fehler statt sein Postfach.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(
      lauf({ status: 'waiting_confirmation', live: false }),
    )

    zeichnen()

    expect(
      await screen.findByText(i18n.t('ai.guardian.waitingApproval')),
    ).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.guardian.running'))).toBeNull()
  })

  it('sieht nach, ob im Hintergrund eine Reparatur anläuft', async () => {
    // Niemand löst hier etwas aus — deshalb muss das Fenster von selbst
    // nachsehen. Ohne den Takt sähe man eine um vier Uhr begonnene Reparatur
    // erst, wenn man die Seite neu lädt.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    zeichnen()

    await waitFor(() => expect(aiApi.getActiveRun).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(21_000)
    await waitFor(() => expect(aiApi.getActiveRun).toHaveBeenCalledTimes(2))
  })

  it('fragt nicht nebenher, während der Strom läuft', async () => {
    // Solange ein Strom hängt, kommt alles über SSE. Eine Abfrage daneben wäre
    // nur Last — und im schlechten Fall ein zweiter, widersprechender Verlauf.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let beenden: (() => void) | undefined
    vi.mocked(attachAiRun).mockReturnValue(new Promise<void>((fertig) => { beenden = () => fertig() }))
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(lauf())

    zeichnen()

    await waitFor(() => expect(attachAiRun).toHaveBeenCalledTimes(1))
    const bisher = vi.mocked(aiApi.getActiveRun).mock.calls.length
    await vi.advanceTimersByTimeAsync(60_000)
    expect(aiApi.getActiveRun).toHaveBeenCalledTimes(bisher)
    beenden?.()
  })

  it('lässt eine Vorschlagskarte bedienbar und weckt danach den Lauf', async () => {
    // Eine Karte zu bestätigen ist keine Nachricht: sie löst nichts ab,
    // sondern weckt den geparkten Lauf dort, wo er steht. Deshalb gibt es hier
    // Karten, obwohl es kein Eingabefeld gibt.
    vi.mocked(aiApi.listActions).mockResolvedValue([vorschlag])
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(lauf({ status: 'waiting_confirmation', live: false }))

    zeichnen()

    fireEvent.click(await screen.findByText('bestaetigen-attrappe'))
    await waitFor(() => expect(attachAiRun).toHaveBeenCalledTimes(1))
    expect(vi.mocked(attachAiRun).mock.calls[0][0]).toBe('lauf-1')
  })

  it('sagt im leeren Fenster, dass es hier nichts zu tun gibt', async () => {
    zeichnen()

    expect(await screen.findByText(i18n.t('ai.guardian.emptyTitle'))).toBeInTheDocument()
  })

  it('übernehmen beendet den Auftrag und öffnet den Chat', async () => {
    // Der ausdrückliche Abbruch — die einzige Stelle, an der man in diesem
    // Fenster etwas auslöst. Danach steht man im Chat, weil man dort tippen
    // kann und hier nicht: wer übernimmt, will als Nächstes etwas sagen.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(lauf())

    zeichnen()

    fireEvent.click(await screen.findByRole('button', { name: /Übernehmen/ }))
    await waitFor(() => expect(aiApi.takeOverGuardian).toHaveBeenCalledTimes(1))
    await waitFor(() => {
      expect(screen.getByTestId('standort')).toHaveTextContent(/^\/ai$/)
    })
  })

  it('bleibt stehen, wenn das Übernehmen scheitert', async () => {
    // Sonst stünde man im Chat und hielte die Reparatur für beendet, während
    // sie im Hintergrund weiterläuft.
    vi.mocked(aiApi.takeOverGuardian).mockRejectedValue(new Error('kaputt'))

    zeichnen()

    fireEvent.click(await screen.findByRole('button', { name: /Übernehmen/ }))
    await waitFor(() => expect(aiApi.takeOverGuardian).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId('standort')).toHaveTextContent('/ai?ansicht=guardian')
  })
})
