import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  aiApi,
  attachAiRun,
  type AiActionProposal,
  type AiAttachment,
  type AiMessage,
  type AiRunSnapshot,
  type AiSkillSummary,
  type AiToolUse,
} from '@/api/ai'
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
  message_id: null,
  redacted_spans: null,
  created_at: '2026-08-01T12:00:00Z',
}

const skills: AiSkillSummary[] = [
  { id: null, skill_key: 'server-nicht-erreichbar', name: 'Nicht erreichbar', description: 'Synthetische Beschreibung fuer den Test', scope: 'shipped', origin: 'shipped', team_id: null, status: 'active', enabled: true, editable: false },
]

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
    vi.mocked(attachAiRun).mockReset().mockResolvedValue(undefined)
  })

  it('offers exactly one conversation and no way to create another', async () => {
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    // Der Kern der Aenderung: kein "Neue Unterhaltung", keine Chatliste.
    expect(screen.queryByRole('button', { name: /neue unterhaltung/i })).not.toBeInTheDocument()
    expect(aiApi.getConversation).toHaveBeenCalledWith()
  })

  it('uploads through the attachment endpoint of the single conversation', async () => {
    render(<AiChat />)
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
    render(<AiChat />)
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
    const ersteAnsicht = render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByLabelText('Denktiefe'))
    fireEvent.click(screen.getByRole('option', { name: 'Hoch' }))
    expect(screen.getByLabelText('Denktiefe')).toHaveTextContent('Hoch')

    // Ein Neuladen der Seite: dieselbe Herkunft, derselbe localStorage, aber
    // ein frisch aufgebauter Baum ohne jeden Zustand von vorhin.
    ersteAnsicht.unmount()
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    expect(screen.getByLabelText('Denktiefe')).toHaveTextContent('Hoch')
  })

  it('faellt auf die Vorgabe zurueck, wenn das Modell die gemerkte Stufe nicht kennt', async () => {
    // Die Stufen sind je Modell verschieden. Eine gemerkte „xhigh" darf nicht
    // stehenbleiben, wo es sie nicht gibt — der Server senkte sie sonst
    // stillschweigend, und die Anzeige loege.
    localStorage.setItem('msm_ai_chat:reasoning:anonym', JSON.stringify({ an: true, stufe: 'xhigh' }))
    render(<AiChat />)
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
    const ersteAnsicht = render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByLabelText('Provider auswählen'))
    fireEvent.click(screen.getByRole('option', { name: /Synthetic Lab/ }))
    expect(screen.getByLabelText('Provider auswählen')).toHaveTextContent('Synthetic Lab')

    ersteAnsicht.unmount()
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    // Ohne das Merken stuende hier wieder das erste benutzbare Modell.
    expect(screen.getByLabelText('Provider auswählen')).toHaveTextContent('Synthetic Lab')
  })

  it('faellt auf das erste benutzbare Modell zurueck, wenn das gemerkte weg ist', async () => {
    // Zwischen zwei Besuchen kann der Provider geloescht, sein Schluessel
    // entfernt oder dem Benutzer die Rolle entzogen worden sein. Ein Verweis
    // darauf ergaebe eine Auswahlliste, deren Wert in keiner Option vorkommt.
    localStorage.setItem('msm_ai_chat:provider:anonym', '99')
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    expect(screen.getByLabelText('Provider auswählen')).toHaveTextContent('Synthetic AI')
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
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    // Die Vorauswahl faellt auf die Vorgabe des Modells, nicht auf nichts —
    // sichtbar am Knopf, bevor die Liste ueberhaupt aufgeklappt wird.
    const stufen = screen.getByLabelText('Denktiefe')
    expect(stufen).toHaveTextContent('Niedrig')

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
    render(<AiChat />)
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
    render(<AiChat />)
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
    render(<AiChat />)
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
    render(<AiChat />)
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
    render(<AiChat />)
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
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await screen.findByText('Antwort wird erstellt …')
    expect(screen.queryByText('Denkt nach …')).not.toBeInTheDocument()
  })

  it('zeigt bei angefordertem Nachdenken den Block statt einer zweiten Wartezeile', async () => {
    const { streamAiMessage } = await import('@/api/ai')
    vi.mocked(streamAiMessage).mockReset().mockImplementation(() => new Promise(() => {}))
    render(<AiChat />)
    await screen.findByText('synthetic-note.txt')

    fireEvent.click(screen.getByLabelText('Denktiefe'))
    fireEvent.click(screen.getByRole('option', { name: 'Hoch' }))
    fireEvent.change(screen.getByLabelText('Nachricht'), { target: { value: 'Was ist los?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    // Der Block ist der Ort, an dem gleich etwas passiert — und der einzige.
    await screen.findByText('Denkt nach …')
    expect(screen.queryByText('Antwort wird erstellt …')).not.toBeInTheDocument()
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
    render(<AiChat />)
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
    render(<AiChat />)
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
    render(<AiChat />)
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
    render(<AiChat />)
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

  it('haengt sich beim Oeffnen an einen Lauf, der noch arbeitet', async () => {
    // Der Fall "ich war auf einer anderen Seite" bzw. "ich habe den Browser
    // neu gestartet". Frueher war der Lauf dann tot; jetzt arbeitet er weiter
    // und der Chat sucht ihn beim Oeffnen.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue({
      id: 'lauf-42', status: 'running', stop_reason: null,
      message_id: 'msg-a', live: true, created_at: '2026-08-01T12:00:00Z',
    })
    vi.mocked(attachAiRun).mockResolvedValue(undefined)

    render(<AiChat />)

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
    })

    render(<AiChat />)

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

    render(<AiChat />)
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

    render(<AiChat />)
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

})
