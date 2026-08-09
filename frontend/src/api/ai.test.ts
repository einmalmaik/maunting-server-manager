import { afterEach, describe, expect, it, vi } from 'vitest'
import { streamAiMessage } from './ai'

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
