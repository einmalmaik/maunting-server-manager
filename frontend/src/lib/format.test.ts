import { describe, expect, it } from 'vitest'
import { formatBytes, formatDauer, formatZeitpunkt } from './format'

describe('formatBytes', () => {
  it('nennt jede Stufe bis TB', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe('3.00 GB')
    // Ein Backup-Archiv erreicht das; früher stand hier „2048.00 GB“.
    expect(formatBytes(2 * 1024 ** 4)).toBe('2.00 TB')
  })

  it('unterscheidet die leere Datei von der unbekannten Größe', () => {
    // Im Messenger hieß eine fehlende Größe bis 02.10.2026 „0 B“.
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(undefined)).toBe('-')
    expect(formatBytes(null)).toBe('-')
    expect(formatBytes(Number.NaN)).toBe('-')
    expect(formatBytes(-1)).toBe('-')
  })
})

describe('formatZeitpunkt', () => {
  const mittag = Date.UTC(2026, 9, 2, 14, 5)

  it('folgt der 12/24-Stunden-Einstellung, wenn sie da ist', () => {
    expect(formatZeitpunkt(mittag, 'en-US', { zeitformat: '24h', utc: true })).toBe('Oct 2, 2026, 14:05')
    expect(formatZeitpunkt(mittag, 'de-DE', { zeitformat: '12h', utc: true })).toMatch(/2:05\s?PM/)
  })

  it('nimmt ohne Einstellung die Gewohnheit der Sprache', () => {
    expect(formatZeitpunkt(mittag, 'de-DE', { utc: true })).toBe('02.10.2026, 14:05')
    expect(formatZeitpunkt(new Date(mittag), 'en-US', { utc: true })).toMatch(/Oct 2, 2026, 2:05\s?PM/)
  })

  it('zeigt eine kaputte Zeit als Strich statt „Invalid Date“', () => {
    expect(formatZeitpunkt(Number.NaN, 'de-DE')).toBe('-')
  })
})

describe('formatDauer', () => {
  it('zeigt Minuten und Sekunden, ab einer Stunde auch Stunden', () => {
    expect(formatDauer(7)).toBe('0:07')
    expect(formatDauer(75.8)).toBe('1:15')
    expect(formatDauer(754)).toBe('12:34')
    expect(formatDauer(3723)).toBe('1:02:03')
  })

  it('macht aus Unsinn keine negative Zeit', () => {
    expect(formatDauer(-5)).toBe('0:00')
    expect(formatDauer(Number.NaN)).toBe('0:00')
    expect(formatDauer(Number.POSITIVE_INFINITY)).toBe('0:00')
  })
})
