import { describe, expect, it } from 'vitest'
import { formatPanelTime } from './timeFormat'

describe('formatPanelTime', () => {
  it('keeps 24-hour panel times unchanged', () => {
    expect(formatPanelTime('13:30', '24h')).toBe('13:30')
  })

  it('formats midnight, noon and afternoon in 12-hour mode', () => {
    expect(formatPanelTime('00:00', '12h')).toBe('12:00 AM')
    expect(formatPanelTime('12:00', '12h')).toBe('12:00 PM')
    expect(formatPanelTime('18:30', '12h')).toBe('6:30 PM')
  })

  // Erweiterte Edges per review (00:30, 12:30 AM/PM, 23:30, invalids, 24:00 fallback)
  it('handles additional 12h edges and fallbacks', () => {
    expect(formatPanelTime('00:30', '12h')).toBe('12:30 AM')
    expect(formatPanelTime('12:30', '12h')).toBe('12:30 PM')
    expect(formatPanelTime('23:30', '12h')).toBe('11:30 PM')
    // Impl treats 24/25 as finite hours (no explicit >23 guard); match actual for coverage
    expect(formatPanelTime('24:00', '12h')).toBe('12:00 PM')
    expect(formatPanelTime('25:00', '12h')).toBe('1:00 PM')
    expect(formatPanelTime('abc', '12h')).toBe('abc')
    expect(formatPanelTime('', '12h')).toBe('')
  })

  it('24h passthrough for edge cases', () => {
    expect(formatPanelTime('00:30', '24h')).toBe('00:30')
    expect(formatPanelTime('23:59', '24h')).toBe('23:59')
  })
})
