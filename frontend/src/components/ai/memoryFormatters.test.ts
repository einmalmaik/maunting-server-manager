import { describe, expect, it } from 'vitest'
import { formatMemoryDate, formatMemoryKey } from './memoryFormatters'

describe('formatMemoryKey', () => {
  it('formats kebab-case with vorliebe prefix to Category: Word with umlaut', () => {
    expect(formatMemoryKey('vorliebe-getraenke')).toBe('Vorliebe: Getränke')
  })

  it('formats snake-case with vorliebe prefix', () => {
    expect(formatMemoryKey('vorliebe_getraenke')).toBe('Vorliebe: Getränke')
  })

  it('formats praeferenz prefix properly', () => {
    expect(formatMemoryKey('praeferenz-sprache')).toBe('Präferenz: Sprache')
    expect(formatMemoryKey('praeferenz_theme')).toBe('Präferenz: Theme')
  })

  it('formats dotted keys into Category: Value style with uppercase acronyms', () => {
    expect(formatMemoryKey('response.language')).toBe('Response: Language')
    expect(formatMemoryKey('ram.bevorzugt')).toBe('RAM: Bevorzugt')
    expect(formatMemoryKey('server.backup_schedule')).toBe('Server: Backup Schedule')
  })

  it('formats mixed dotted and colon keys with proper separation', () => {
    expect(formatMemoryKey('server:backup.frequency')).toBe('Server: Backup: Frequency')
    expect(formatMemoryKey('ai.service.model')).toBe('AI: Service: Model')
  })

  it('formats camelCase keys into Title Case', () => {
    expect(formatMemoryKey('favoriteDrink')).toBe('Favorite Drink')
    expect(formatMemoryKey('apiKey')).toBe('API Key')
    expect(formatMemoryKey('uiThemeMode')).toBe('UI Theme Mode')
  })

  it('formats standard kebab-case without category to Title Case', () => {
    expect(formatMemoryKey('start-timeout')).toBe('Start Timeout')
    expect(formatMemoryKey('max-connections')).toBe('Max Connections')
  })

  it('formats standard snake-case without category to Title Case', () => {
    expect(formatMemoryKey('auto_save_interval')).toBe('Auto Save Interval')
    expect(formatMemoryKey('theme_preference')).toBe('Theme Preference')
  })

  it('formats single words with capitalization and transliterations', () => {
    expect(formatMemoryKey('zeitzone')).toBe('Zeitzone')
    expect(formatMemoryKey('anrede')).toBe('Anrede')
    expect(formatMemoryKey('whitelist')).toBe('Whitelist')
    expect(formatMemoryKey('ports')).toBe('Ports')
    expect(formatMemoryKey('oberflaeche')).toBe('Oberfläche')
    expect(formatMemoryKey('schriftgroesse')).toBe('Schriftgröße')
  })

  it('handles empty and whitespace strings gracefully', () => {
    expect(formatMemoryKey('')).toBe('')
    expect(formatMemoryKey('   ')).toBe('')
  })
})

describe('formatMemoryDate', () => {
  it('formats valid ISO date strings with locale', () => {
    const formattedDe = formatMemoryDate('2026-08-01T12:00:00Z', 'de-DE')
    expect(formattedDe).toBe('01.08.2026')
  })

  it('returns empty string for null, undefined or invalid dates', () => {
    expect(formatMemoryDate(null)).toBe('')
    expect(formatMemoryDate(undefined)).toBe('')
    expect(formatMemoryDate('not-a-date')).toBe('')
  })
})
