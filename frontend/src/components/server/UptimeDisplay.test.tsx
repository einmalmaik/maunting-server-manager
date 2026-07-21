import { describe, expect, it } from 'vitest'
import { formatCompactUptime } from './UptimeDisplay'

describe('formatCompactUptime', () => {
  it('formats long running servers without a noisy seconds counter', () => {
    expect(formatCompactUptime(3 * 86_400 + 14 * 3_600 + 26 * 60)).toBe('3d 14h 26m')
    expect(formatCompactUptime(2 * 3_600 + 8 * 60)).toBe('2h 8m')
  })

  it('keeps unknown and short values explicit', () => {
    expect(formatCompactUptime(null)).toBe('-')
    expect(formatCompactUptime(42)).toBe('0m')
  })
})
