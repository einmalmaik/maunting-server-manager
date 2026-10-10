import { afterEach, describe, expect, it, vi } from 'vitest'
import { aiApi, streamAiMessage } from './ai'
import * as client from './client'

describe('streamAiMessage', () => {
  afterEach(() => vi.restoreAllMocks())

  it('parses fragmented CRLF SSE frames in order', async () => {
    const encoder = new TextEncoder()
    const chunks = [
      'event: message\r\ndata: {"message_id":"m1",',
      '"request_id":"r1"}\r\n\r\nevent: delta\r\ndata: {"content":"Hal',
      'lo"}\r\n\r\nevent: done\r\ndata: {"message_id":"m1"}\r\n\r\n',
    ]
    const body = new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)))
        controller.close()
      },
    })
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(body, { status: 200 }))
    const events: string[] = []

    await streamAiMessage({
      content: 'Hi',
      provider_id: 1,
      request_id: '00000000-0000-0000-0000-000000000002',
      reasoning: false,
    }, (event) => events.push(event.event))

    expect(events).toEqual(['message', 'delta', 'done'])
  })

  it('rejects malformed frames without exposing their content as an error', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(
      'event: delta\ndata: not-secret-json\n\n',
      { status: 200 },
    ))

    await expect(streamAiMessage({
      content: 'Hi', provider_id: 1, request_id: 'request', reasoning: false,
    }, () => undefined)).rejects.toThrow('AI_STREAM_INVALID')
  })

  it('skips unknown event names instead of tearing the answer down', async () => {
    // Ein Bündel, das ein Panelupdate überlebt hat, kennt ein neu
    // eingeführtes Ereignis noch nicht. Es zu übergehen kostet nichts; ein
    // Wurf riss die halb geschriebene Antwort ab und markierte sie als
    // fehlgeschlagen, während der Lauf serverseitig weiterlief.
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(
      'event: delta\ndata: {"content":"Hallo"}\n\n'
      + 'event: neues_ereignis\ndata: {"irgendwas":1}\n\n'
      + 'event: done\ndata: {"message_id":"m1"}\n\n',
      { status: 200 },
    ))
    const events: string[] = []

    await streamAiMessage({
      content: 'Hi', provider_id: 1, request_id: 'request', reasoning: false,
    }, (event) => events.push(event.event))

    expect(events).toEqual(['delta', 'done'])
  })

  it('accepts a minimized action proposal event', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(
      'event: proposal\ndata: {"id":"p1","conversation_id":"c1","server_id":2,"tool_name":"propose_backup","preview":{"operation":"backup"},"expected_revision":null,"requires_confirmation":true,"status":"proposed","task_id":null,"error_code":null,"created_at":"2026-08-01T12:00:00Z"}\n\n',
      { status: 200 },
    ))
    const events: string[] = []

    await streamAiMessage({
      content: 'Backup', provider_id: 1, request_id: 'request', reasoning: false,
    }, (event) => events.push(event.event))

    expect(events).toEqual(['proposal'])
  })

  it('passes reasoning and tool frames through as their own events', async () => {
    // Denkschritte und Werkzeugaufrufe sind keine Antwort. Wuerden sie als
    // `delta` ankommen, stuenden sie mitten im Antworttext.
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(
      'event: reasoning\ndata: {"content":"Ich pruefe die Ports"}\n\n'
      + 'event: tool\ndata: {"tool_name":"read_server_ports","server_id":4}\n\n'
      + 'event: delta\ndata: {"content":"Port 25565 ist offen."}\n\n',
      { status: 200 },
    ))
    const events: string[] = []

    await streamAiMessage({
      content: 'Ports?', provider_id: 1, request_id: 'request', reasoning: true,
    }, (event) => events.push(event.event))

    expect(events).toEqual(['reasoning', 'tool', 'delta'])
  })
})

describe('aiApi memory URLs', () => {
  afterEach(() => vi.restoreAllMocks())

  /**
   * Die Bereiche des Gedächtnisses unterscheiden sich in der Abfrage und
   * nirgends sonst. Ein Aufruf, der seinen Bezug unterwegs verliert, trifft
   * deshalb nicht „ein bisschen daneben" — er trifft einen anderen Bereich
   * oder gar keinen.
   *
   * Geprüft wird die gebaute Adresse und nicht die Argumente des Aufrufers.
   * `clearMemory` hatte `serverId` als einziges der Memory-Kommandos nie
   * durchgereicht; ein Test auf die Argumente wäre dabei grün geblieben,
   * während das Backend 404 antwortet.
   */
  it.each([
    ['user', () => aiApi.clearMemory('user'), '/ai/memory?scope=user'],
    ['team', () => aiApi.clearMemory('team', 7), '/ai/memory?scope=team&team_id=7'],
    ['server_shared', () => aiApi.clearMemory('server_shared', undefined, 62),
      '/ai/memory?scope=server_shared&server_id=62'],
  ])('leert den Bereich %s unter der richtigen Adresse', async (_name, aufruf, adresse) => {
    const gerufen = vi.spyOn(client, 'api').mockResolvedValue({ removed: 0 })

    await aufruf()

    expect(gerufen).toHaveBeenCalledWith(adresse, { method: 'DELETE' })
  })

  it('holt das Wissen eines Servers unter seiner Nummer', async () => {
    const gerufen = vi.spyOn(client, 'api').mockResolvedValue([])

    await aiApi.listMemory('server_shared', 62)

    expect(gerufen).toHaveBeenCalledWith('/ai/memory?scope=server_shared&server_id=62')
  })

  it('blättert mit Ansicht und Themen, gleichnamige Themen als zwei Kennungen', async () => {
    const gerufen = vi.spyOn(client, 'api').mockResolvedValue({ entries: [], total: 0, clearable: 0, limit: 200 })

    await aiApi.listScopeMemory('team', undefined, 7, 200, { status: 'vergessen', thema: ['a', 'b'] })
    await aiApi.listPersonalMemory()

    expect(gerufen).toHaveBeenNthCalledWith(
      1, '/ai/memory/page?scope=team&team_id=7&offset=200&status=vergessen&thema=a&thema=b',
    )
    expect(gerufen).toHaveBeenNthCalledWith(2, '/ai/memory/personal?offset=0&status=aktiv')
  })

  it('legt an, ändert mit Fassung und holt zurück — jeweils unter der Kennung', async () => {
    const gerufen = vi.spyOn(client, 'api').mockResolvedValue({})

    await aiApi.createMemory({ scope: 'user', text: 'Ein Satz.', titel: null, thema: 'Stil' })
    await aiApi.updateMemory('e1', { text: 'Neu.', fassung: 3 })
    await aiApi.restoreMemory('e1')
    await aiApi.restoreMemoryVersion('e1', 'f1', 4)

    expect(gerufen).toHaveBeenNthCalledWith(1, '/ai/memory', {
      method: 'POST', body: JSON.stringify({ scope: 'user', text: 'Ein Satz.', titel: null, thema: 'Stil' }),
    })
    expect(gerufen).toHaveBeenNthCalledWith(2, '/ai/memory/e1', {
      method: 'PATCH', body: JSON.stringify({ text: 'Neu.', fassung: 3 }),
    })
    expect(gerufen).toHaveBeenNthCalledWith(3, '/ai/memory/e1/zurueckholen', { method: 'POST' })
    expect(gerufen).toHaveBeenNthCalledWith(4, '/ai/memory/e1/fassungen/f1/zurueckholen', {
      method: 'POST', body: JSON.stringify({ fassung: 4 }),
    })
  })
})
