import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  aiApi,
  attachAiRun,
  type AiActionProposal,
  type AiAttachment,
  type AiContextStatus,
  type AiMessage,
  type AiRunSnapshot,
  type AiSkillSummary,
  type AiToolUse,
} from '@/api/ai'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { AI_ZUSTELLUNG_EVENT } from '@/lib/aiZustellung'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiChat } from './AiChat'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...original,
    aiApi: {
      listProviders: vi.fn(),
      getConversation: vi.fn(),
      getActiveRun: vi.fn(),
      clearHistory: vi.fn(),
      editMessage: vi.fn(),
      listActions: vi.fn(),
      listAttachments: vi.fn(),
      listSkills: vi.fn(),
      uploadAttachment: vi.fn(),
      deleteAttachment: vi.fn(),
      listAutonomyGrants: vi.fn(),
      saveAutonomyGrant: vi.fn(),
      getContextStatus: vi.fn(),
      listWorkers: vi.fn(),
      typing: vi.fn(),
    },
    streamAiMessage: vi.fn(),
    attachAiRun: vi.fn(),
  }
})

vi.mock('@/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/client')>()
  return { ...original, api: vi.fn() }
})

// Statt einer Attrappe, die nichts kann: ein Knopf, der genau das ausloest, was
// die echte Karte nach Bestaetigen und Ausfuehren tut. Ohne ihn liesse sich die
// wichtigste Zusicherung dieses Bildschirms nicht pruefen — dass es nach dem
// Bestaetigen weitergeht.
vi.mock('./AiActionProposalCard', () => ({
  AiActionProposalCard: ({
    proposal, onChange,
  }: { proposal: AiActionProposal; onChange: (p: AiActionProposal) => void }) => (
    <button type="button" onClick={() => onChange({ ...proposal, status: 'succeeded' })}>
      bestaetigen-attrappe
    </button>
  ),
}))

const CONVERSATION = {
  id: '00000000-0000-0000-0000-000000000301',
  // Der Chat ist das `primary`-Fenster. Die Guardian-Reparaturen liegen
  // daneben in einer eigenen Unterhaltung und kommen hier nie an.
  kind: 'primary' as const,
  title: 'KI-Assistent',
  created_at: '2026-08-01T12:00:00Z',
  updated_at: '2026-08-01T12:00:00Z',
  has_more: false,
}

const attachment: AiAttachment = {
  id: '00000000-0000-0000-0000-000000000303',
  conversation_id: CONVERSATION.id,
  original_name: 'synthetic-note.txt',
  media_type: 'text/plain',
  size_bytes: 24,
  status: 'ready',
  rejection_code: null,
  message_id: null,
  redacted_spans: null,
  created_at: '2026-08-01T12:00:00Z',
}

const skills: AiSkillSummary[] = [
  { id: null, skill_key: 'server-nicht-erreichbar', name: 'Nicht erreichbar', description: 'Synthetische Beschreibung fuer den Test', scope: 'shipped', origin: 'shipped', team_id: null, status: 'active', enabled: true, editable: false },
]

/** Wie voll der Kontext ist — die Zahlen hinter dem Ring am Absendeknopf. */
const kontextStand: AiContextStatus = {
  known: true, window_tokens: 128_000, usable_tokens: 100_000, used_tokens: 1_000,
  compaction_percent: 75, summarized: false,
}

/** Eine bereits gesendete eigene Nachricht — die Grundlage fuers Bearbeiten. */
const eigeneNachricht: AiMessage = {
  id: 'msg-user', role: 'user', content: 'urspruengliche Frage', reasoning: null,
  question: null, status: 'complete', provider_id: null, model: null,
  created_at: '2026-08-01T12:00:00Z',
}

describe('AiChat', () => {
  beforeEach(async () => {
    Element.prototype.scrollIntoView = vi.fn()
    // Der Chat merkt sich Modell und Denkstufe im localStorage. Ohne dieses
    // Leeren truege ein Test die Wahl des vorigen in den naechsten — genau
    // das, was die Speicherung im Browser fuer den Benutzer ja bewirken soll.
    localStorage.clear()
    // Angemeldet ist hier niemand: die gemerkte Wahl liegt dann unter
    // `anonym`. Ein Test, der jemanden anmeldet, muss den nächsten deshalb
    // wieder allein lassen — sonst suchte der seine Schlüssel woanders.
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: false })
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
      {
        id: 1, name: 'Synthetic AI', default_model: 'test-model',
        requires_api_key: false, operator_key_available: true, available: true,
        // Ein Modell mit Stufen — der Fall, den 127 der 402 Katalogmodelle
        // treffen. Die Liste kommt vom Server bereits auf die Rolle geklemmt.
        reasoning: true, efforts: ['low', 'medium', 'high'],
        can_disable: true, default_effort: 'medium',
      },
    ])
    vi.mocked(aiApi.getConversation).mockReset().mockResolvedValue({ ...CONVERSATION, messages: [eigeneNachricht] })
    vi.mocked(aiApi.listActions).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.listAttachments).mockReset().mockResolvedValue([attachment])
    vi.mocked(aiApi.listSkills).mockReset().mockResolvedValue(skills)
    vi.mocked(aiApi.uploadAttachment).mockReset().mockResolvedValue(attachment)
    vi.mocked(aiApi.clearHistory).mockReset().mockResolvedValue(undefined)
    // Nichts laeuft mehr von vorhin. Der Chat fragt das beim Oeffnen, um
    // sich an einen weiterlaufenden Lauf wieder anhaengen zu koennen.
    vi.mocked(aiApi.getActiveRun).mockReset().mockResolvedValue(null)
    vi.mocked(aiApi.getContextStatus).mockReset().mockResolvedValue(kontextStand)
    // Keine Hintergrund-Auftraege: die Worker-Leiste bleibt dann unsichtbar,
    // und der Chat sieht aus wie vor ihrer Einfuehrung.
    vi.mocked(aiApi.listWorkers).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.typing).mockReset().mockResolvedValue(undefined)
    vi.mocked(attachAiRun).mockReset().mockResolvedValue(undefined)
  })

  it('offers exactly one conversation and no way to create another', async () => {
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    // Der Kern der Aenderung: kein "Neue Unterhaltung", keine Chatliste.
    expect(screen.queryByRole('button', { name: /neue unterhaltung/i })).not.toBeInTheDocument()
    expect(aiApi.getConversation).toHaveBeenCalledWith()
  })

  it('holt die Serverliste nicht ohne Autonomierecht', async () => {
    // Die Liste wird an genau einer Stelle gebraucht: im Autonomie-Knopf. Ohne
    // `ai.autonomous.use` wird der gar nicht gezeichnet — die Abfrage holte
    // trotzdem alle sichtbaren Server samt Bind-IP, Spiel-, Query- und
    // RCON-Port in den Browser und warf sie weg.
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    expect(client.api).not.toHaveBeenCalled()
  })

  it('holt die Serverliste mit Autonomierecht', async () => {
    // Das Gegenstück: wo der Knopf steht, braucht er die Namen.
    usePermissionsStore.setState({
      me: {
        is_owner: false, role_id: null, role_name: null,
        global_keys: ['ai.chat.use', 'ai.attachments.use', 'ai.skills.use', 'ai.autonomous.use'],
        server_keys: {},
      },
      isLoading: false,
      error: null,
    })
    vi.mocked(aiApi.listAutonomyGrants).mockResolvedValue([])
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    await waitFor(() => expect(client.api).toHaveBeenCalledWith('/servers'))
  })

  it('uploads through the attachment endpoint of the single conversation', async () => {
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    const file = new File(['synthetic content'], 'another-synthetic.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText('Sicheren Anhang hinzufügen'), { target: { files: [file] } })

    await waitFor(() => expect(aiApi.uploadAttachment).toHaveBeenCalledWith(file))
  })

  it('sends the chosen reasoning level along with the message', async () => {
    // Aus dem Schalter ist eine Stufenwahl geworden. Die Stufen stehen nicht
    // im Code, sondern kommen je Modell vom Server — gemessen gibt es bei
    // OpenRouter 20 verschiedene Stufenlisten, eine feste Skala waere falsch.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockResolvedValue(undefined)
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByLabelText('Denktiefe'))
    expect(screen.getAllByRole('option').map((o) => o.textContent))
      .toEqual(['Kein Nachdenken', 'Niedrig', 'Mittel', 'Hoch'])

    fireEvent.click(screen.getByRole('option', { name: 'Hoch' }))
    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Hallo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    // Beide Felder gehen mit: der Boolean, weil 145 der 272 denkenden Modelle
    // nur an/aus koennen, und die Stufe fuer die uebrigen.
    await waitFor(() => expect(streamAiMessage).toHaveBeenCalledWith(
      expect.objectContaining({ content: 'Hallo', reasoning: true, reasoning_effort: 'high' }),
      expect.any(Function),
      expect.any(AbortSignal),
    ))
  })

  it('haelt die gewaehlte Denkstufe ueber ein Neuladen hinweg', async () => {
    // Der eigentliche Fehler: nach F5 stand wieder „Kein Nachdenken" da,
    // gleichgueltig was vorher gewaehlt war. Die Wahl gilt fuer die naechste
    // Frage und nicht fuer die vorige Antwort — sie gehoert deshalb in den
    // Browser und nicht in die Unterhaltung.
    const ersteAnsicht = render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByLabelText('Denktiefe'))
    fireEvent.click(screen.getByRole('option', { name: 'Hoch' }))
    expect(screen.getByLabelText('Denktiefe')).toHaveTextContent('Hoch')

    // Ein Neuladen der Seite: dieselbe Herkunft, derselbe localStorage, aber
    // ein frisch aufgebauter Baum ohne jeden Zustand von vorhin.
    ersteAnsicht.unmount()
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    expect(screen.getByLabelText('Denktiefe')).toHaveTextContent('Hoch')
  })

  it('faellt auf die Vorgabe zurueck, wenn das Modell die gemerkte Stufe nicht kennt', async () => {
    // Die Stufen sind je Modell verschieden. Eine gemerkte „xhigh" darf nicht
    // stehenbleiben, wo es sie nicht gibt — der Server senkte sie sonst
    // stillschweigend, und die Anzeige loege.
    localStorage.setItem('msm_ai_chat:reasoning:anonym', JSON.stringify({ an: true, stufe: 'xhigh' }))
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    expect(screen.getByLabelText('Denktiefe')).toHaveTextContent('Mittel')
  })

  it('haelt das gewaehlte Modell ueber ein Neuladen hinweg', async () => {
    vi.mocked(aiApi.listProviders).mockResolvedValue([
      {
        id: 1, name: 'Synthetic AI', default_model: 'test-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: true, efforts: ['low', 'medium', 'high'],
        can_disable: true, default_effort: 'medium',
      },
      {
        id: 2, name: 'Synthetic Lab', default_model: 'lab-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: false, efforts: [], can_disable: true, default_effort: null,
      },
    ])
    const ersteAnsicht = render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByLabelText('Provider auswählen'))
    fireEvent.click(screen.getByRole('option', { name: /Synthetic Lab/ }))
    expect(screen.getByLabelText('Provider auswählen')).toHaveTextContent('Synthetic Lab')

    ersteAnsicht.unmount()
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    // Ohne das Merken stuende hier wieder das erste benutzbare Modell.
    expect(screen.getByLabelText('Provider auswählen')).toHaveTextContent('Synthetic Lab')
  })

  it('faellt auf das erste benutzbare Modell zurueck, wenn das gemerkte weg ist', async () => {
    // Zwischen zwei Besuchen kann der Provider geloescht, sein Schluessel
    // entfernt oder dem Benutzer die Rolle entzogen worden sein. Ein Verweis
    // darauf ergaebe eine Auswahlliste, deren Wert in keiner Option vorkommt.
    localStorage.setItem('msm_ai_chat:provider:anonym', '99')
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    expect(screen.getByLabelText('Provider auswählen')).toHaveTextContent('Synthetic AI')
  })

  it('merkt die Wahl unter der Benutzerkennung und liest keine fremde', async () => {
    // Warum die Kennung im Schlüssel steht: localStorage gehört der Herkunft
    // und nicht der Anmeldung. An einem festen Schlüssel fände der nächste
    // Benutzer am selben Rechner Modell und Denkstufe des vorigen vor — und
    // beide hängen an dessen Rolle (welche Provider er sehen darf, welche
    // Stufen für ihn freigegeben sind), nicht an seiner.
    //
    // `aiChatPreferences.test.ts` prüft, dass zwei Kennungen zwei Schlüssel
    // ergeben. Hier steht die andere Hälfte: dass der Chat überhaupt die
    // Kennung des angemeldeten Benutzers einsetzt.
    useAuthStore.setState({
      user: { id: 42, username: 'test' } as any,
      isAuthenticated: true,
      isLoading: false,
    })
    // Was ein anderer an diesem Rechner hinterlassen hat.
    localStorage.setItem('msm_ai_chat:provider:anonym', '2')
    localStorage.setItem('msm_ai_chat:reasoning:anonym', JSON.stringify({ an: true, stufe: 'low' }))
    vi.mocked(aiApi.listProviders).mockResolvedValue([
      {
        id: 1, name: 'Synthetic AI', default_model: 'test-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: true, efforts: ['low', 'medium', 'high'],
        can_disable: true, default_effort: 'medium',
      },
      {
        id: 2, name: 'Synthetic Lab', default_model: 'lab-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: false, efforts: [], can_disable: true, default_effort: null,
      },
    ])

    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    // Nichts davon wird gelesen: sonst stünde hier „Synthetic Lab" und
    // „Niedrig" statt der Vorgaben für einen Benutzer ohne gemerkte Wahl.
    expect(screen.getByLabelText('Provider auswählen')).toHaveTextContent('Synthetic AI')
    expect(screen.getByLabelText('Denktiefe')).toHaveTextContent('Kein Nachdenken')

    // Erst die Denktiefe, dann das Modell: „Synthetic Lab" denkt gar nicht,
    // danach gäbe es die Stufenwahl nicht mehr.
    fireEvent.click(screen.getByLabelText('Denktiefe'))
    fireEvent.click(screen.getByRole('option', { name: 'Hoch' }))
    fireEvent.click(screen.getByLabelText('Provider auswählen'))
    fireEvent.click(screen.getByRole('option', { name: /Synthetic Lab/ }))

    expect(localStorage.getItem('msm_ai_chat:reasoning:42'))
      .toBe(JSON.stringify({ an: true, stufe: 'high' }))
    expect(localStorage.getItem('msm_ai_chat:provider:42')).toBe('2')
    // Und der fremde Eintrag bleibt, wie er war — nicht überschrieben.
    expect(localStorage.getItem('msm_ai_chat:reasoning:anonym'))
      .toBe(JSON.stringify({ an: true, stufe: 'low' }))
    expect(localStorage.getItem('msm_ai_chat:provider:anonym')).toBe('2')
  })

  it('nimmt die Wahl am Konto vor der im Browser und schreibt sie beim Wechsel dorthin', async () => {
    // Die Wahl am Konto ist die eine Quelle, die auch Overlay und Desktop-App
    // sehen — den localStorage dieses Fensters kennen die nicht. Ohne den
    // Vorrang liefe die App still auf einem anderen Modell als das Panel.
    useAuthStore.setState({
      user: { id: 42, username: 'test', ai_provider_id: 2 } as any,
      isAuthenticated: true,
      isLoading: false,
    })
    localStorage.setItem('msm_ai_chat:provider:42', '1')
    vi.mocked(aiApi.listProviders).mockResolvedValue([
      {
        id: 1, name: 'Synthetic AI', default_model: 'test-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: true, efforts: ['low', 'medium', 'high'],
        can_disable: true, default_effort: 'medium',
      },
      {
        id: 2, name: 'Synthetic Lab', default_model: 'lab-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: false, efforts: [], can_disable: true, default_effort: null,
      },
    ])
    vi.mocked(client.api).mockResolvedValue({ ai_provider_id: 1 })

    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    // Das Konto sagt 2, der Browser 1 — das Konto gewinnt.
    expect(screen.getByLabelText('Provider auswählen')).toHaveTextContent('Synthetic Lab')

    fireEvent.click(screen.getByLabelText('Provider auswählen'))
    fireEvent.click(screen.getByRole('option', { name: /Synthetic AI/ }))

    // Der Wechsel geht ans Konto zurück (PATCH) und in den Auth-Store, damit
    // der Sprachmodus derselben Seite sofort die neue Wahl sieht.
    await waitFor(() => expect(client.api).toHaveBeenCalledWith('/auth/me/ai-provider', {
      method: 'PATCH',
      body: JSON.stringify({ provider_id: 1 }),
    }))
    await waitFor(() => expect(useAuthStore.getState().user?.ai_provider_id).toBe(1))
  })

  it('lässt den Kontextring nicht von der Antwort des alten Modells überschreiben', async () => {
    // Zwei Modellwechsel kurz hintereinander — etwa beim Vergleichen. Die
    // Auskunft ist bei kaltem Modellkatalog ein externer Abruf, die langsamere
    // Antwort für das erste Modell kann also nach der des zweiten eintreffen.
    // Der Ring zeigte dann ein 128k-Fenster für ein 1M-Modell an — genau die
    // Zahl, an der man abliest, wann gefaltet wird.
    vi.mocked(aiApi.listProviders).mockResolvedValue([
      {
        id: 1, name: 'Synthetic AI', default_model: 'test-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: true, efforts: ['low', 'medium', 'high'],
        can_disable: true, default_effort: 'medium',
      },
      {
        id: 2, name: 'Synthetic Lab', default_model: 'lab-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: false, efforts: [], can_disable: true, default_effort: null,
      },
    ])
    let antworteAltesModell: (stand: AiContextStatus) => void = () => {}
    vi.mocked(aiApi.getContextStatus).mockReset().mockImplementation((id: number) => (
      id === 1
        ? new Promise<AiContextStatus>((resolve) => { antworteAltesModell = resolve })
        : Promise.resolve({
            known: true, window_tokens: 1_000_000, usable_tokens: 500_000,
            used_tokens: 50_000, compaction_percent: 75, summarized: false,
          })
    ))
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    // Weiter zum zweiten Modell, während die Auskunft zum ersten noch hängt.
    fireEvent.click(screen.getByLabelText('Provider auswählen'))
    fireEvent.click(screen.getByRole('option', { name: /Synthetic Lab/ }))
    await screen.findByRole('img', { name: /50k Token \(10 %\)/ })

    // Jetzt erst antwortet das erste Modell — mit einem fast vollen Fenster.
    await act(async () => { antworteAltesModell({
      known: true, window_tokens: 128_000, usable_tokens: 100_000,
      used_tokens: 90_000, compaction_percent: 75, summarized: false,
    }) })

    // Stehenbleiben muss der Stand des gewählten Modells.
    expect(screen.getByRole('img', { name: /Belegt/ }))
      .toHaveAccessibleName(expect.stringContaining('50k Token (10 %)'))
  })

  it('bietet bei einem Modell mit Denkzwang kein „aus" an', async () => {
    // 82 der 402 Katalogmodelle tragen `mandatory: true` — dort ist Nachdenken
    // nicht abschaltbar. Ein „aus" in der Liste waere eine Wahl, die der
    // Server anschliessend stillschweigend zuruecknehmen muesste.
    vi.mocked(aiApi.listProviders).mockResolvedValue([
      {
        id: 1, name: 'Synthetic AI', default_model: 'test-model',
        requires_api_key: false, operator_key_available: true, available: true,
        reasoning: true, efforts: ['low', 'high'],
        can_disable: false, default_effort: 'low',
      },
    ])
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    // Die Vorauswahl faellt auf die Vorgabe des Modells, nicht auf nichts —
    // sichtbar am Knopf, bevor die Liste ueberhaupt aufgeklappt wird. Das
    // Nachziehen laeuft in einem eigenen Effekt (`denkwahlFuer` haengt am
    // geladenen Provider) — deshalb `waitFor` statt eines synchronen Blicks
    // direkt nach dem ersten sichtbaren Render.
    const stufen = screen.getByLabelText('Denktiefe')
    await waitFor(() => expect(stufen).toHaveTextContent('Niedrig'))

    fireEvent.click(stufen)
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Niedrig', 'Hoch'])
  })
  it('zeigt bei einem abgelehnten Werkzeugaufruf einen Satz statt eines Schlüssels', async () => {
    // Der Betreiber sah `ai.errors.codes.AI_TOOL_REJECTED` in der Meldung. Der
    // Chat gibt einen zweistufigen Rückfall mit, und der passende deutsche Satz
    // stand seit jeher in der Sprachdatei — nur verwarf
    // `parseMissingKeyHandler` jeden `defaultValue`, weil er einarmig war.
    //
    // Der Test greift bewusst am Toast-Speicher ab und nicht an einer Attrappe:
    // was hier steht, ist genau das, was der Benutzer liest.
    const { streamAiMessage } = await import('@/api/ai')
    const { useToastStore } = await import('@/stores/toastStore')
    useToastStore.setState({ toasts: [] })
    vi.mocked(streamAiMessage).mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'error', data: {
        code: 'AI_TOOL_REJECTED', message_key: 'ai.chat.errors.toolRejected',
      } })
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Lösch den Server' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1))
    const meldung = useToastStore.getState().toasts[0].message
    expect(meldung).not.toMatch(/^ai\./)
    expect(meldung).toBe(i18n.t('ai.chat.errors.toolRejected'))
  })

  it('nennt im Verlauf den Skill-Namen statt des Werkzeugnamens', async () => {
    // "read_skill" sagt niemandem etwas. Der Betreiber will sehen, *welche*
    // erlernte Vorgehensweise gegriffen hat — sonst wirkt eine daraus
    // entstandene Antwort wie geraten.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'tool', data: {
        tool_name: 'read_skill', server_id: null,
        skill_key: 'server-nicht-erreichbar', skill_name: 'Nicht erreichbar',
        skill_status: null, skill_learned: false,
      } })
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Hilfe' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await screen.findByText('Skill „Nicht erreichbar“ genutzt')
  })

  it('macht sichtbar, dass ein gelernter Skill noch auf Freigabe wartet', async () => {
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'tool', data: {
        tool_name: 'learn_skill', server_id: null,
        skill_key: 'valheim-ram', skill_name: 'Valheim braucht 6 GB',
        skill_status: 'pending', skill_learned: true,
      } })
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Hilfe' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    // Ohne diesen Zusatz haette der Benutzer den Eindruck, der Skill wirke bereits.
    await screen.findByText(/wartet auf Freigabe/)
  })
  it('zeigt den Denktext im Block und nicht nur den Block', async () => {
    // Bis hierher prüfte kein einziger Test, dass **Text** im Denkblock
    // landet — nur, dass es ihn gibt. Genau daran hätte der Umbau von einem
    // flachen Feld auf einen Abschnitt still scheitern können.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'reasoning', data: { content: 'Ich pruefe die Ports.' } })
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await screen.findByText('Ich pruefe die Ports.')
  })

  it('zeichnet die Gedanken jeder Runde an ihrer Stelle', async () => {
    // **Der eigentliche Fehler.** Der Denktext war ein flaches Feld neben den
    // Abschnitten, also gab es genau eine mögliche Stelle für ihn: ganz oben.
    // Die Gedanken der dritten Runde standen damit über dem Text der ersten,
    // der dort seit zwölf Sekunden stand.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'reasoning', data: { content: 'Ich pruefe die Ports.' } })
      onEvent({ event: 'tool', data: {
        tool_name: 'read_server_logs', server_id: 7,
        skill_key: null, skill_name: null, skill_status: null, skill_learned: false,
      } })
      onEvent({ event: 'reasoning', data: { content: 'Jetzt die Logs.' } })
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    const zweiter = await screen.findByText('Jetzt die Logs.')
    const werkzeug = screen.getByText('Logs gelesen')
    // Die Reihenfolge ist die Information: der zweite Gedanke steht **hinter**
    // dem Werkzeug, nicht in einem wachsenden Kasten davor.
    expect(werkzeug.compareDocumentPosition(zweiter))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING)

    // Die erste Runde ist vorbei: ihr Block sagt "Nachgedacht" und steht zu.
    // Aufgeklappt liegt ihr Text vor dem Werkzeug — dort, wo er entstanden ist.
    fireEvent.click(screen.getByRole('button', { name: /Nachgedacht/ }))
    const erster = await screen.findByText('Ich pruefe die Ports.')
    expect(erster.compareDocumentPosition(werkzeug))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('zeigt keinen Denkblock, wenn Nachdenken ausgeschaltet ist', async () => {
    // Der Block behauptete "Denkt nach …" auch in der Voreinstellung „aus",
    // obwohl nie ein Zeichen kommen konnte — und darunter stand gleichzeitig
    // "Antwort wird erstellt …". Zwei Anzeigen für dieselbe Wartezeit, eine
    // davon gelogen.
    const { streamAiMessage } = await import('@/api/ai')
    // Der Lauf hängt: genau der Zustand, in dem beide Anzeigen standen.
    vi.mocked(streamAiMessage).mockReset().mockImplementation(() => new Promise(() => {}))
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await screen.findByText('Antwort wird erstellt …')
    expect(screen.queryByText('Denkt nach …')).not.toBeInTheDocument()
  })

  it('zeigt auch bei angefordertem Nachdenken keinen leeren Denkblock', async () => {
    // Der Kasten hing an der Auswahlliste, nicht am Denktext: "denken" an
    // hieß Kasten da, auch wenn nie ein Denkzeichen kam. Er behauptete damit
    // eine Überlegung, die nicht stattfand — und war für die Wartezeit die
    // schlechtere Auskunft als ein Satz, der sagt, was gerade läuft.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementation(() => new Promise(() => {}))
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByLabelText('Denktiefe'))
    fireEvent.click(screen.getByRole('option', { name: 'Hoch' }))
    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    // Solange nichts bekannt ist, steht der allgemeine Hinweis da — genau
    // einer, und kein Kasten daneben.
    await screen.findByText('Antwort wird erstellt …')
    expect(screen.queryByText('Denkt nach …')).not.toBeInTheDocument()
  })

  it('zeigt den Denkblock, sobald wirklich Denktext fließt', async () => {
    // Die Gegenprobe zum Test darüber: der Block ist nicht abgeschafft, er
    // hängt nur nicht mehr an der Auswahlliste.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'reasoning', data: { content: 'Ich pruefe die Ports.' } })
      await new Promise(() => {})
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await screen.findByText('Denkt nach …')
    await screen.findByText('Ich pruefe die Ports.')
  })

  it('sagt beim angekündigten Werkzeug, woran gerade gearbeitet wird', async () => {
    // Der Kern der Sache: `tool_plan` meldet, was gleich läuft — bevor es
    // läuft. Vorher stand während der längsten Stille des Ablaufs nur
    // "Antwort wird erstellt …", also nichts.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'tool_plan', data: { aufrufe: [
        { call_id: 'call-1', tool_name: 'list_tasks', server_id: null },
        { call_id: 'call-2', tool_name: 'read_server_logs', server_id: 7 },
      ] } })
      await new Promise(() => {})
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    // Zwei Werkzeuge, zwei Zeilen — und der allgemeine Hinweis weicht ihnen.
    await screen.findByText('Ich sehe mir die Aufgabenliste an')
    await screen.findByText('Ich lese die Logs')
    expect(screen.queryByText('Antwort wird erstellt …')).not.toBeInTheDocument()
  })

  it('fasst denselben Werkzeugnamen zu einer Zeile zusammen', async () => {
    // Zwei `read_config` für zwei Dateien sind zwei Aufrufe, aber ein Satz:
    // der Unterschied liegt allein in den Argumenten, und die dürfen hier
    // nicht stehen. Zweimal derselbe Satz sähe aus wie ein Fehler.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'tool_plan', data: { aufrufe: [
        { call_id: 'call-1', tool_name: 'read_server_logs', server_id: 7 },
        { call_id: 'call-2', tool_name: 'read_server_logs', server_id: 8 },
      ] } })
      await new Promise(() => {})
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await screen.findByText('Ich lese die Logs')
    expect(screen.getAllByText('Ich lese die Logs')).toHaveLength(1)
  })

  it('nimmt die Ankündigung zurück, sobald das Werkzeug gelaufen ist', async () => {
    // Sonst behauptete "Ich lese die Logs" eine Arbeit, die längst vorbei ist
    // — und stünde nach einem Fehlschlag für immer da.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'tool_plan', data: { aufrufe: [
        { call_id: 'call-1', tool_name: 'read_server_logs', server_id: 7 },
      ] } })
      onEvent({ event: 'tool', data: {
        tool_name: 'read_server_logs', server_id: 7,
        skill_key: null, skill_name: null, skill_status: null, skill_learned: false,
      } })
      await new Promise(() => {})
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    // Die Vergangenheitsform steht im Verlauf, die Verlaufsform ist weg —
    // und darunter genau eine Wartezeile, nicht zwei.
    await screen.findByText('Logs gelesen')
    expect(screen.queryByText('Ich lese die Logs')).not.toBeInTheDocument()
    await screen.findByText('Antwort wird erstellt …')
    expect(screen.getAllByText('Antwort wird erstellt …')).toHaveLength(1)
  })

  it('zeigt die Gedanken einer alten Nachricht weiterhin an', async () => {
    // Nachrichten aus der Zeit vor den Denkabschnitten tragen sie nur flach.
    // Ohne den Rückfall wären sie nach dem Umbau stillschweigend verschwunden.
    vi.mocked(aiApi.getConversation).mockResolvedValue({
      ...CONVERSATION,
      messages: [eigeneNachricht, {
        id: 'msg-alt', role: 'assistant', content: 'Alles läuft.',
        reasoning: 'Damals nachgedacht.', question: null, status: 'complete',
        provider_id: 1, model: 'test-model', created_at: '2026-08-01T12:00:01Z',
      }],
    })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByRole('button', { name: /Nachgedacht/ }))
    await screen.findByText('Damals nachgedacht.')
  })

  it('nimmt beim Bearbeiten die alte Fassung zurueck, bevor neu gesendet wird', async () => {
    // Der Verlaufsschnitt muss *vor* dem Senden passieren. Andersherum stuende
    // die verworfene Fassung noch im Kontext, und die KI wuerde eine Frage
    // beruecksichtigen, die der Benutzer gerade zurueckgenommen hat.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(aiApi.editMessage).mockResolvedValue({ removed: 2 })
    vi.mocked(streamAiMessage).mockResolvedValue(undefined)
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Nachricht bearbeiten' }))
    fireEvent.change(screen.getByLabelText('Nachricht bearbeiten'), {
      target: { value: 'so war es gemeint' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Neu senden' }))

    await waitFor(() => expect(aiApi.editMessage).toHaveBeenCalledWith(
      'msg-user', 'so war es gemeint',
    ))
    await waitFor(() => expect(streamAiMessage).toHaveBeenCalledWith(
      expect.objectContaining({ content: 'so war es gemeint' }),
      expect.any(Function),
      expect.any(AbortSignal),
    ))
  })

  it('sendet nicht, wenn der Schnitt fehlschlaegt', async () => {
    // Sonst haette der Benutzer eine neue Antwort auf einen Verlauf, der die
    // alte Fassung noch enthaelt.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(aiApi.editMessage).mockRejectedValue(new Error('offline'))
    vi.mocked(streamAiMessage).mockReset().mockResolvedValue(undefined)
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Nachricht bearbeiten' }))
    fireEvent.change(screen.getByLabelText('Nachricht bearbeiten'), {
      target: { value: 'neuer Text' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Neu senden' }))

    await waitFor(() => expect(aiApi.editMessage).toHaveBeenCalled())
    expect(streamAiMessage).not.toHaveBeenCalled()
  })
  it('zeigt eine Rueckfrage als Karte und sendet den Klick als Nachricht', async () => {
    // Ein Klick ist der geringere Widerstand als Tippen — deshalb sendet er
    // sofort. Wer etwas dranschreiben will, nutzt weiterhin das Eingabefeld.
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementationOnce(async (_p, onEvent) => {
      onEvent({ event: 'question', data: {
        question: 'Welche Minecraft-Version soll es sein?',
        options: [
          { label: '1.20.1', hint: 'am weitesten verbreitet' },
          { label: '1.21.4', hint: null },
        ],
      } })
    }).mockResolvedValue(undefined)
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'server anlegen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await screen.findByText('Welche Minecraft-Version soll es sein?')
    expect(screen.getByText('am weitesten verbreitet')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /1\.20\.1/ }))

    await waitFor(() => expect(streamAiMessage).toHaveBeenCalledWith(
      expect.objectContaining({ content: '1.20.1' }),
      expect.any(Function),
      expect.any(AbortSignal),
    ))
    // Die Karte bleibt stehen, aber ohne Knoepfe: sonst saehe man spaeter nur
    // die Antwort und wuesste nicht mehr, worauf sie sich bezieht.
    await screen.findByText('Beantwortet.')
  })

  /**
   * Den Verlaufskasten für die Scrollprüfungen vorbereiten.
   *
   * jsdom rechnet kein Layout: `scrollHeight`, `clientHeight` und `scrollTop`
   * sind dort immer 0, und der Kasten stünde damit rechnerisch stets schon
   * ganz unten. Die drei Zahlen müssen deshalb gesetzt werden — sonst prüft
   * der Test nichts.
   */
  function bereiteVerlauf(kasten: HTMLElement, start: number) {
    let stand = start
    Object.defineProperty(kasten, 'clientHeight', { value: 300, configurable: true })
    Object.defineProperty(kasten, 'scrollHeight', { value: 2000, configurable: true })
    Object.defineProperty(kasten, 'scrollTop', {
      configurable: true,
      get: () => stand,
      set: (wert: number) => { stand = wert },
    })
    return () => stand
  }

  it('reißt den Verlauf nicht ans Ende, wenn der Benutzer hochgescrollt hat', async () => {
    // **Die gemeldete Beobachtung.** Während einer langen Antwort zurück zu
    // einem früheren Absatz oder einer Werkzeugzeile zu blättern war
    // unmöglich: das nächste Textstück zog die Ansicht sofort wieder ans Ende.
    const { streamAiMessage } = await import('@/api/ai')
    let melde: (event: { event: 'delta'; data: { content: string } }) => void = () => {}
    vi.mocked(streamAiMessage).mockReset().mockImplementation((_payload, onEvent) => {
      melde = onEvent as typeof melde
      return new Promise(() => {})
    })
    const { container } = render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    const kasten = container.querySelector('[aria-live="polite"]') as HTMLElement
    const standJetzt = bereiteVerlauf(kasten, 0)

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))
    await screen.findByText('Antwort wird erstellt …')

    // Der Benutzer blättert zurück.
    kasten.scrollTop = 100
    fireEvent.scroll(kasten)

    act(() => melde({ event: 'delta', data: { content: 'Ein weiterer Absatz.' } }))
    await screen.findByText('Ein weiterer Absatz.')

    expect(standJetzt()).toBe(100)
  })

  it('schiebt weiter nach, solange der Verlauf unten steht', async () => {
    // Das Gegenstück: wer nicht wegblättert, will die Antwort mitlaufen sehen.
    // Ohne diesen Fall wäre der Fix oben ein abgeschaltetes Nachziehen.
    const { streamAiMessage } = await import('@/api/ai')
    let melde: (event: { event: 'delta'; data: { content: string } }) => void = () => {}
    vi.mocked(streamAiMessage).mockReset().mockImplementation((_payload, onEvent) => {
      melde = onEvent as typeof melde
      return new Promise(() => {})
    })
    const { container } = render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    const kasten = container.querySelector('[aria-live="polite"]') as HTMLElement
    const standJetzt = bereiteVerlauf(kasten, 0)

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))
    await screen.findByText('Antwort wird erstellt …')

    // Fast unten heißt unten — dieselben 50 Pixel Spielraum wie in der Konsole.
    kasten.scrollTop = 1680
    fireEvent.scroll(kasten)

    act(() => melde({ event: 'delta', data: { content: 'Ein weiterer Absatz.' } }))
    await screen.findByText('Ein weiterer Absatz.')

    expect(standJetzt()).toBe(2000)
  })

  it('haengt sich beim Oeffnen an einen Lauf, der noch arbeitet', async () => {
    // Der Fall "ich war auf einer anderen Seite" bzw. "ich habe den Browser
    // neu gestartet". Frueher war der Lauf dann tot; jetzt arbeitet er weiter
    // und der Chat sucht ihn beim Oeffnen.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue({
      id: 'lauf-42', status: 'running', stop_reason: null,
      message_id: 'msg-a', live: true, created_at: '2026-08-01T12:00:00Z',
      kind: 'primary', conversation_id: CONVERSATION.id, server_id: null,
    })
    vi.mocked(attachAiRun).mockResolvedValue(undefined)

    render(<MemoryRouter><AiChat /></MemoryRouter>)

    await waitFor(() => {
      expect(attachAiRun).toHaveBeenCalledWith('lauf-42', expect.any(Function), expect.any(AbortSignal))
    })
  })

  it('haengt sich nicht an einen Lauf, den dieser Prozess nicht mehr kennt', async () => {
    // Nach einem Neustart des Panels steht ein geparkter Lauf zwar noch in der
    // Datenbank, aber niemand haelt ihn im Speicher. Ein Ladebalken, der sich
    // nie bewegt, waere die schlechtere Antwort als gar keiner.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue({
      id: 'lauf-43', status: 'waiting_confirmation', stop_reason: 'awaiting_confirmation',
      message_id: null, live: false, created_at: '2026-08-01T12:00:00Z',
      kind: 'primary', conversation_id: CONVERSATION.id, server_id: null,
    })

    render(<MemoryRouter><AiChat /></MemoryRouter>)

    await screen.findByLabelText('Nachricht')
    expect(attachAiRun).not.toHaveBeenCalled()
  })

  it('arbeitet nach dem Bestaetigen weiter, ohne dass man etwas schreiben muss', async () => {
    // **Die Beschwerde aus dem Betrieb.** Vorher lief die Aktion, und der Chat
    // blieb stumm — man musste eine neue Nachricht schicken, damit die KI
    // ueberhaupt erfuhr, wie ihr eigener Vorschlag ausgegangen ist.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(null)
    vi.mocked(aiApi.listActions).mockResolvedValue([{
      id: 'vorschlag-1',
      conversation_id: CONVERSATION.id,
      server_id: 7,
      tool_name: 'propose_backup',
      preview: {},
      expected_revision: null,
      requires_confirmation: true,
      autonomous: false,
      reason: null,
      expected_effect: null,
      status: 'proposed',
      task_id: null,
      error_code: null,
      run_id: 'lauf-7',
      created_at: '2026-08-01T12:00:01Z',
    }])
    vi.mocked(attachAiRun).mockResolvedValue(undefined)

    render(<MemoryRouter><AiChat /></MemoryRouter>)
    const knopf = await screen.findByText('bestaetigen-attrappe')
    fireEvent.click(knopf)

    await waitFor(() => {
      expect(attachAiRun).toHaveBeenCalledWith('lauf-7', expect.any(Function), expect.any(AbortSignal))
    })
  })

  it('zeigt eine Werkzeugzeile nach dem erneuten Anhängen nicht doppelt', async () => {
    // Der Abzug eines Laufs trägt **alle** seine Abschnitte. Wer sich nach
    // einer bestätigten Aktion wieder anhängt, bekommt sie deshalb erneut.
    //
    // Früher waren Werkzeuge eigene Verlaufseinträge mit selbst vergebenen
    // Kennungen, und die Dublettenprüfung dagegen war handgemacht: trugen live
    // gemeldete und aus dem Abzug gelesene Zeilen verschiedene Nummern, stand
    // jede vorher gezeigte Zeile danach zweimal da. Seit Werkzeuge Abschnitte
    // **innerhalb** der Nachricht sind, wird die Liste aus dem Abzug gesetzt
    // statt angehängt — die Dublette kann strukturell nicht mehr entstehen.
    // Der Test bleibt trotzdem: er hält fest, dass das Setzen und nicht das
    // Anhängen die richtige Verknüpfung ist.
    const werkzeug: AiToolUse = {
      tool_name: 'read_server_logs', server_id: 7,
      skill_key: null, skill_name: null, skill_status: null, skill_learned: false,
    }
    const abzug = (tools: AiToolUse[]): AiRunSnapshot => ({
      run_id: 'lauf-9', status: 'running', message_id: null, content: '',
      reasoning: '',
      sections: tools.map((werkzeug) => ({ art: 'tool' as const, werkzeug })),
      question: null, proposals: [], stop_reason: null,
    })
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(aiApi.listActions).mockResolvedValue([{
      id: 'vorschlag-9',
      conversation_id: CONVERSATION.id,
      server_id: 7,
      tool_name: 'propose_backup',
      preview: {},
      expected_revision: null,
      requires_confirmation: true,
      autonomous: false,
      reason: null,
      expected_effect: null,
      status: 'proposed',
      task_id: null,
      error_code: null,
      run_id: 'lauf-9',
      created_at: '2026-08-01T12:00:01Z',
    }])
    vi.mocked(streamAiMessage).mockReset().mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'snapshot', data: abzug([]) })
      onEvent({ event: 'tool', data: werkzeug })
      onEvent({ event: 'run', data: { run_id: 'lauf-9', status: 'waiting_confirmation' } })
    })
    // Beim Wiederanhängen steht dasselbe Werkzeug im Abzug — genau die Zeile,
    // die eben schon live gemeldet wurde.
    vi.mocked(attachAiRun).mockReset().mockImplementation(async (_id, onEvent) => {
      onEvent({ event: 'snapshot', data: abzug([werkzeug]) })
    })

    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))
    await screen.findByText('Logs gelesen')

    fireEvent.click(screen.getByText('bestaetigen-attrappe'))
    await waitFor(() => expect(attachAiRun).toHaveBeenCalledWith(
      'lauf-9', expect.any(Function), expect.any(AbortSignal),
    ))

    await waitFor(() => expect(screen.getAllByText('Logs gelesen')).toHaveLength(1))
  })

  it('holt eine fremde Nachricht binnen eines Taktes — ohne Ereignis, ohne Neuladen', async () => {
    // **Der gemeldete Fehler.** Eine Nachricht aus einem zweiten Tab oder der
    // Desktop-App stand in der Datenbank und nirgendwo auf dem Schirm, bis
    // jemand hart neu lud: die Glocke pollt nur alle 60 Sekunden, und ein
    // kurzer fremder Lauf begann und endete komplett zwischen zwei Blicken.
    // Der Chat sieht deshalb jetzt selbst nach — im Guardian-Takt.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      render(<MemoryRouter><AiChat /></MemoryRouter>)
      await screen.findByText('synthetic-note.txt')

      // Jetzt legt der zweite Tab eine Antwort in die Unterhaltung.
      vi.mocked(aiApi.getConversation).mockResolvedValue({
        ...CONVERSATION,
        messages: [eigeneNachricht, {
          id: 'msg-fremd', role: 'assistant', content: 'Antwort aus dem zweiten Tab.',
          reasoning: null, question: null, status: 'complete',
          provider_id: 1, model: 'test-model', created_at: '2026-08-01T12:00:02Z',
        }],
      })

      await vi.advanceTimersByTimeAsync(21_000)
      await screen.findByText('Antwort aus dem zweiten Tab.')
    } finally {
      vi.useRealTimers()
    }
  })

  it('ein überholtes Nachladen überschreibt den laufenden Strom nicht', async () => {
    // Die zweite Hälfte desselben Fehlers: beginnt zwischen Fetch-Start und
    // -Auflösung ein Strom, trüge die späte Antwort den alten Verlauf über
    // die optimistischen Blasen — und die folgenden Deltas liefen ins Leere,
    // weil `useAiLauf.aendere` die Nachrichten-ID nicht mehr findet. Die
    // Nachricht „verschwand" dann bis zum harten Neuladen.
    const { streamAiMessage } = await import('@/api/ai')
    let melde: (event: { event: 'delta'; data: { content: string } }) => void = () => {}
    vi.mocked(streamAiMessage).mockReset().mockImplementation((_payload, onEvent) => {
      melde = onEvent as typeof melde
      return new Promise(() => {})
    })
    let antworteSpaet: (stand: Awaited<ReturnType<typeof aiApi.getConversation>>) => void = () => {}
    vi.mocked(aiApi.getConversation).mockReset()
      .mockResolvedValueOnce({ ...CONVERSATION, messages: [eigeneNachricht] })
      .mockImplementationOnce(() => new Promise((resolve) => { antworteSpaet = resolve }))
      .mockResolvedValue({ ...CONVERSATION, messages: [eigeneNachricht] })
    render(<MemoryRouter><AiChat /></MemoryRouter>)
    await screen.findByText('synthetic-note.txt')

    // Ein Nachladen beginnt (Zustellung) — und bleibt unterwegs hängen …
    act(() => { window.dispatchEvent(new CustomEvent(AI_ZUSTELLUNG_EVENT)) })
    // … während der Benutzer sendet und der Strom zu zeichnen anfängt.
    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Neue Frage' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))
    act(() => melde({ event: 'delta', data: { content: 'Erste Zeile der Antwort.' } }))
    await screen.findByText('Erste Zeile der Antwort.')

    // Jetzt erst trifft die alte Fassung ein — ohne die Frage von eben.
    await act(async () => {
      antworteSpaet({ ...CONVERSATION, messages: [eigeneNachricht] })
    })

    // Beides bleibt stehen: die optimistische Frage und der halbe Zug.
    expect(screen.getByText('Neue Frage')).toBeInTheDocument()
    expect(screen.getByText('Erste Zeile der Antwort.')).toBeInTheDocument()
  })

})
