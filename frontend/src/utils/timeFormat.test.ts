import { describe, expect, it } from 'vitest'
import { formatDurationSeconds, formatPanelDateTime } from './timeFormat'

describe('timeFormat', () => {
  it('formats uptime as total hours, minutes, and seconds', () => {
    expect(formatDurationSeconds(0)).toBe('00:00:00')
    expect(formatDurationSeconds(3661)).toBe('01:01:01')
    expect(formatDurationSeconds(90061)).toBe('25:01:01')
    expect(formatDurationSeconds(null)).toBe('-')
  })

  it('respects panel 12h/24h display for timestamps', () => {
    const value = '2026-06-01T13:05:06Z'
    expect(formatPanelDateTime(value, '24h', 'en-US')).not.toMatch(/AM|PM/)
    expect(formatPanelDateTime(value, '12h', 'en-US')).toMatch(/AM|PM/)
  })
})
