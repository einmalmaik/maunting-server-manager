import { describe, expect, it } from 'vitest'
import {
  containsSecretFragment,
  formatAuditTarget,
  formatAuditTime,
  mapAuditApiRows,
  safeAuditDetails,
} from './auditPresentation'

describe('auditPresentation', () => {
  it('maps valid API rows and drops invalid ones', () => {
    const rows = mapAuditApiRows([
      {
        id: 3,
        user_id: 1,
        action: 'postgres.admin.rotate',
        target_type: 'managed_postgres',
        target_id: null,
        origin: 'ai',
        correlation_id: '0a613465-487d-44a0-af1c-5aa031a873c9',
        details: '{"nodes_updated":[1]}',
        created_at: '2026-07-30T12:00:00Z',
      },
      { id: 'x', action: 'bad' },
      null,
      { id: 4, action: '' },
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0].action).toBe('postgres.admin.rotate')
    expect(rows[0].user_id).toBe(1)
    expect(rows[0].origin).toBe('ai')
    expect(rows[0].correlation_id).toBe('0a613465-487d-44a0-af1c-5aa031a873c9')
  })

  it('formats target and time without inventing data', () => {
    expect(formatAuditTarget({ target_type: 'server', target_id: 12 })).toBe('server #12')
    expect(formatAuditTarget({ target_type: null, target_id: null })).toBe('—')
    expect(formatAuditTime(null)).toBe('—')
    expect(formatAuditTime('not-a-date')).toBe('—')
  })

  it('never leaves raw password-like fragments unredacted in details helper', () => {
    const raw = 'password=super-secret-value token=abc'
    expect(containsSecretFragment(raw)).toBe(true)
    const safe = safeAuditDetails(raw)
    expect(safe).not.toContain('super-secret-value')
    expect(safe.toLowerCase()).toContain('[redacted]')
  })

  it('returns empty list for non-array payloads', () => {
    expect(mapAuditApiRows(null)).toEqual([])
    expect(mapAuditApiRows({ items: [] })).toEqual([])
  })
})
