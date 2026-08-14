import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  attachAiRun,
  streamAiMessage,
  type AiRunSnapshot,
  type AiSection,
  type AiStreamEvent,
  type AiToolUse,
} from '@/api/ai'
import '@/i18n'
import { useAiLauf, type Entry } from './useAiLauf'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...original,
    aiApi: { listAttachments: vi.fn() },
    streamAiMessage: vi.fn(),
    attachAiRun: vi.fn(),
  }
})

/**
 * Der Sinn dieser Datei ist die Grenze selbst.
 *
 * Dieselben Zusicherungen ließen sich früher nur über die vollständig
 * gerenderte Oberfläche prüfen — mit sieben gemockten Endpunkten, einem
 * Providerkatalog und einer Anhängeliste, nur um ein einziges Ereignis
 * durchzureichen. Hier ist ein Lauf ein Lauf: Ereignisse rein, Verlauf raus.
 */
const werkzeug: AiToolUse = {
  tool_name: 'read_server_logs', server_id: 7,
  skill_key: null, skill_name: null, skill_status: null, skill_learned: false,
}

function abzug(abschnitte: AiSection[]): AiRunSnapshot {
  return {
    run_id: 'lauf-1', status: 'running', message_id: 'msg-a', content: '',
    reasoning: '', sections: abschnitte, question: null, proposals: [],
    stop_reason: null,
  }
}

function baueLauf() {
  return renderHook(() => useAiLauf({
    providerId: 1,
    canAttach: false,
    denken: { an: false, stufe: null },
    ladeKontext: async () => {},
    setAttachments: () => {},
  }))
}

/** Die Antwortblasen des Verlaufs — die Frage des Benutzers bleibt draußen. */
function antworten(entries: Entry[]) {
  return entries.flatMap((entry) => (
    entry.kind === 'message' && entry.message.role === 'assistant' ? [entry.message] : []
  ))
}

describe('useAiLauf', () => {
  beforeEach(() => {
    vi.mocked(streamAiMessage).mockReset().mockResolvedValue(undefined)
    vi.mocked(attachAiRun).mockReset().mockResolvedValue(undefined)
  })

  it('setzt die Abschnitte aus dem Abzug, statt sie anzuhängen', async () => {
    // Ein Abzug ist die **vollständige** Antwort bis hierher. Wer sich zweimal
    // anhängt, sähe beim Anhängen jede Werkzeugzeile doppelt.
    vi.mocked(attachAiRun).mockImplementation(async (_id, onEvent) => {
      onEvent({ event: 'snapshot', data: abzug([{ art: 'tool', werkzeug }]) })
      onEvent({ event: 'snapshot', data: abzug([{ art: 'tool', werkzeug }]) })
    })
    const { result } = baueLauf()

    await act(async () => { await result.current.haengeAn('lauf-1') })

    expect(antworten(result.current.entries)).toHaveLength(1)
    expect(antworten(result.current.entries)[0].sections).toHaveLength(1)
  })

  it('fängt nach einem `segment` eine neue Nachricht an', async () => {
    // Eine Fortsetzung nach einer bestätigten Aktion gehört nicht mehr in die
    // Blase davor: der Text davor ist abgeschlossen und darf nicht weiterwachsen.
    vi.mocked(streamAiMessage).mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'message', data: { message_id: 'msg-a', request_id: 'r-1' } })
      onEvent({ event: 'delta', data: { content: 'Erster Absatz.' } })
      onEvent({ event: 'segment', data: { run_id: 'lauf-1' } })
      onEvent({ event: 'message', data: { message_id: 'msg-b', request_id: 'r-1' } })
      onEvent({ event: 'delta', data: { content: 'Zweiter Absatz.' } })
    })
    const { result } = baueLauf()

    await act(async () => { await result.current.sendContent('Was ist los?') })

    expect(antworten(result.current.entries).map((m) => [m.id, m.content])).toEqual([
      ['msg-a', 'Erster Absatz.'],
      ['msg-b', 'Zweiter Absatz.'],
    ])
  })

  it('verwirft Textstücke, solange keine Nachricht läuft', async () => {
    // Beim Anhängen gibt es keine optimistische Blase. Käme ein `delta` vor
    // dem Abzug, wüsste niemand, an welche Antwort es gehört — eine erfundene
    // Blase wäre die schlechtere Auskunft als gar keine.
    vi.mocked(attachAiRun).mockImplementation(async (_id, onEvent) => {
      onEvent({ event: 'delta', data: { content: 'Text ohne Blase.' } })
    })
    const { result } = baueLauf()

    await act(async () => { await result.current.haengeAn('lauf-1') })

    expect(result.current.entries).toHaveLength(0)
  })

  it('merkt sich die angekündigten Werkzeuge, bis eines gelaufen ist', async () => {
    // `tool_plan` ist flüchtig und wird deshalb **nicht** zum Abschnitt: es ist
    // keine Tatsache über die Antwort, sondern eine Anzeige während der Arbeit.
    let melde: (event: AiStreamEvent) => void = () => {}
    vi.mocked(streamAiMessage).mockImplementation(async (_payload, onEvent) => {
      melde = onEvent
      await new Promise(() => {})
    })
    const { result } = baueLauf()

    void act(() => { void result.current.sendContent('Was ist los?') })
    await act(async () => {
      melde({ event: 'message', data: { message_id: 'msg-a', request_id: 'r-1' } })
      melde({ event: 'tool_plan', data: { aufrufe: [
        { call_id: 'call-1', tool_name: 'read_server_logs', server_id: 7 },
      ] } })
    })

    expect(result.current.laufendeWerkzeuge.map((a) => a.tool_name)).toEqual(['read_server_logs'])
    expect(antworten(result.current.entries)[0].sections ?? []).toHaveLength(0)

    await act(async () => { melde({ event: 'tool', data: werkzeug }) })

    // Ab hier steht die Vergangenheitsform im Verlauf; die Ankündigung hat
    // nichts mehr zu melden.
    expect(result.current.laufendeWerkzeuge).toEqual([])
    expect(antworten(result.current.entries)[0].sections).toHaveLength(1)
  })

  it('nimmt beim fertigen Werkzeug nur dessen Ankündigung zurück', async () => {
    // Bis zu acht Werkzeuge laufen gleichzeitig. Räumte das erste fertige die
    // ganze Ansage ab, verlören die übrigen ihre Zeile — und die Oberfläche
    // behauptete, sie tue nichts Bestimmtes mehr, während sieben noch arbeiten.
    let melde: (event: AiStreamEvent) => void = () => {}
    vi.mocked(streamAiMessage).mockImplementation(async (_payload, onEvent) => {
      melde = onEvent
      await new Promise(() => {})
    })
    const { result } = baueLauf()

    void act(() => { void result.current.sendContent('Was ist los?') })
    await act(async () => {
      melde({ event: 'message', data: { message_id: 'msg-a', request_id: 'r-1' } })
      melde({ event: 'tool_plan', data: { aufrufe: [
        { call_id: 'call-1', tool_name: 'read_server_logs', server_id: 7 },
        { call_id: 'call-2', tool_name: 'list_tasks', server_id: null },
      ] } })
    })

    // `werkzeug` ist der Logabruf — die Aufgabenliste läuft weiter.
    await act(async () => { melde({ event: 'tool', data: werkzeug }) })

    expect(result.current.laufendeWerkzeuge.map((a) => a.call_id)).toEqual(['call-2'])
  })

  it('lässt keine Ankündigung stehen, wenn der Lauf endet', async () => {
    // Ein Lauf, der an einem Werkzeug hängenbleibt, ist der Normalfall und
    // nicht die Ausnahme. Ohne dieses Leeren stünde "Ich lese die Logs" nach
    // einem Fehlschlag bis zum nächsten Neuladen da.
    vi.mocked(streamAiMessage).mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'tool_plan', data: { aufrufe: [
        { call_id: 'call-1', tool_name: 'read_server_logs', server_id: 7 },
      ] } })
      onEvent({ event: 'error', data: {
        code: 'AI_PROVIDER_UNAVAILABLE', message_key: 'ai.chat.errors.stream',
      } })
    })
    const { result } = baueLauf()

    await act(async () => { await result.current.sendContent('Was ist los?') })

    expect(result.current.laufendeWerkzeuge).toEqual([])
  })

  it('lässt nach einem Fehler keine Blase als schreibend stehen', async () => {
    // Sonst dreht sich der Ladepunkt weiter, und der Benutzer wartet auf eine
    // Antwort, die nie kommt.
    vi.mocked(streamAiMessage).mockImplementation(async (_payload, onEvent) => {
      onEvent({ event: 'error', data: {
        code: 'AI_TOOL_REJECTED', message_key: 'ai.chat.errors.toolRejected',
      } })
    })
    const { result } = baueLauf()

    await act(async () => { await result.current.sendContent('Lösch den Server') })

    expect(antworten(result.current.entries).map((m) => m.status)).toEqual(['failed'])
    expect(result.current.streaming).toBe(false)
  })
})
