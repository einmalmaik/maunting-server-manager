import { afterEach, describe, expect, it, vi } from 'vitest'
import { latestAiSkillVersions, streamAiMessage, type AiSkill } from './ai'

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

    await streamAiMessage('00000000-0000-0000-0000-000000000001', {
      content: 'Hi',
      provider_id: 1,
      request_id: '00000000-0000-0000-0000-000000000002',
    }, (event) => events.push(event.event))

    expect(events).toEqual(['message', 'delta', 'done'])
  })

  it('rejects malformed frames without exposing their content as an error', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(
      'event: delta\ndata: not-secret-json\n\n',
      { status: 200 },
    ))

    await expect(streamAiMessage('conversation', {
      content: 'Hi', provider_id: 1, request_id: 'request',
    }, () => undefined)).rejects.toThrow('AI_STREAM_INVALID')
  })

  it('accepts a minimized action proposal event', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(
      'event: proposal\ndata: {"id":"p1","conversation_id":"c1","server_id":2,"tool_name":"propose_backup","preview":{"operation":"backup"},"expected_revision":null,"requires_confirmation":true,"status":"proposed","task_id":null,"error_code":null,"created_at":"2026-08-01T12:00:00Z"}\n\n',
      { status: 200 },
    ))
    const events: string[] = []

    await streamAiMessage('conversation', {
      content: 'Backup', provider_id: 1, request_id: 'request',
    }, (event) => events.push(event.event))

    expect(events).toEqual(['proposal'])
  })
})

describe('latestAiSkillVersions', () => {
  it('keeps only the newest immutable version for each skill key', () => {
    const base = {
      name: 'Status check', description: 'Synthetic test workflow', steps: [], enabled: true,
      created_by: 1, created_at: '2026-08-01T12:00:00Z',
    }
    const rows: AiSkill[] = [
      { ...base, id: 'old', skill_key: 'status.check', version: 1 },
      { ...base, id: 'new', skill_key: 'status.check', version: 2 },
      { ...base, id: 'other', skill_key: 'capacity.check', version: 1 },
    ]

    expect(latestAiSkillVersions(rows).map((skill) => skill.id)).toEqual(['new', 'other'])
  })
})
