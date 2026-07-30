import { describe, expect, it } from 'vitest'
import {
  formatRotateSuccessSummary,
  mapRotateAdminResult,
  rotateResultLeaksSecret,
} from './securityRotatePresentation'

describe('securityRotatePresentation', () => {
  it('maps a clean rotate response without password fields', () => {
    const result = mapRotateAdminResult({
      ok: true,
      admin_user: 'msm_admin',
      nodes_updated: [1, 2],
      nodes_skipped: [3],
    })
    expect(result.ok).toBe(true)
    expect(result.admin_user).toBe('msm_admin')
    expect(result.nodes_updated).toEqual([1, 2])
    expect(formatRotateSuccessSummary(result)).toContain('Nodes aktualisiert: 2')
    expect(formatRotateSuccessSummary(result).toLowerCase()).not.toMatch(/password\s*[:=]/)
  })

  it('rejects payloads that leak secret fields', () => {
    expect(
      rotateResultLeaksSecret({
        ok: true,
        password: 'should-not-appear',
      }),
    ).toBe(true)
    expect(() =>
      mapRotateAdminResult({
        ok: true,
        password: 'should-not-appear',
        admin_user: 'msm_admin',
        nodes_updated: [],
        nodes_skipped: [],
      }),
    ).toThrow(/Secret/i)
  })

  it('throws on empty or invalid API bodies', () => {
    expect(() => mapRotateAdminResult(null)).toThrow()
    expect(() => mapRotateAdminResult('nope')).toThrow()
  })
})
