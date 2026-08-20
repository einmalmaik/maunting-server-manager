import { describe, expect, it } from 'vitest'
import { formatMessageTime, mergeEntries, gruppiert } from './AiVerlauf'
import type { AiMessage, AiActionProposal, AiSection } from '@/api/ai'

describe('formatMessageTime', () => {
  it('returns empty string for null or undefined or invalid date', () => {
    expect(formatMessageTime(null)).toBe('')
    expect(formatMessageTime(undefined)).toBe('')
    expect(formatMessageTime('')).toBe('')
    expect(formatMessageTime('invalid-date')).toBe('')
  })

  it('formats today correctly with HH:MM', () => {
    const now = new Date()
    const result = formatMessageTime(now.toISOString())
    const expected = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    expect(result).toBe(expected)
  })

  it('formats yesterday with Gestern prefix', () => {
    const yesterday = new Date()
    yesterday.setDate(yesterday.getDate() - 1)
    const result = formatMessageTime(yesterday.toISOString())
    expect(result).toContain('Gestern')
  })

  it('formats older dates with day and month or full year', () => {
    const older = new Date(2025, 0, 15, 10, 30)
    const result = formatMessageTime(older.toISOString())
    expect(result).toContain('15.01.')
  })
})

describe('mergeEntries', () => {
  it('sorts messages and proposals chronologically', () => {
    const msg1: AiMessage = {
      id: 'm1',
      role: 'user',
      content: 'Hello',
      reasoning: null,
      question: null,
      status: 'complete',
      provider_id: null,
      model: null,
      created_at: '2026-08-20T10:00:00Z',
    }
    const prop1: AiActionProposal = {
      id: 'p1',
      conversation_id: 'c1',
      server_id: 1,
      run_id: 'r1',
      tool_name: 'propose_config_patch',
      preview: {},
      expected_revision: null,
      requires_confirmation: true,
      autonomous: false,
      reason: 'Patch file',
      expected_effect: null,
      status: 'proposed',
      task_id: null,
      error_code: null,
      created_at: '2026-08-20T10:05:00Z',
    }
    const msg2: AiMessage = {
      id: 'm2',
      role: 'assistant',
      content: 'Done',
      reasoning: null,
      question: null,
      status: 'complete',
      provider_id: null,
      model: null,
      created_at: '2026-08-20T10:10:00Z',
    }

    const merged = mergeEntries([msg2, msg1], [prop1])
    expect(merged.map((e) => e.id)).toEqual(['m1', 'p1', 'm2'])
  })
})

describe('gruppiert', () => {
  it('groups consecutive tool sections together', () => {
    const sections: AiSection[] = [
      { art: 'text', inhalt: 'First' },
      { art: 'tool', werkzeug: { tool_name: 'list_my_servers', server_id: null } },
      { art: 'tool', werkzeug: { tool_name: 'read_config', server_id: 1 } },
      { art: 'text', inhalt: 'Second' },
    ]

    const teile = gruppiert(sections)
    expect(teile).toHaveLength(3)
    expect(teile[0]).toEqual({ art: 'text', inhalt: 'First' })
    expect(teile[1].art).toBe('tools')
    if (teile[1].art === 'tools') {
      expect(teile[1].werkzeuge).toHaveLength(2)
    }
    expect(teile[2]).toEqual({ art: 'text', inhalt: 'Second' })
  })
})
