import { beforeEach, describe, expect, it, vi } from 'vitest'

// `clearSqlConsoleHistory` wird hier bewusst nicht direkt eingebunden: der
// Test unten ruft `logout()` und prueft damit zugleich, dass der authStore die
// Aufraeumfunktion ueberhaupt aufruft. Ein Direktaufruf haette genau diese
// Verdrahtung nicht mitgeprueft — und sie ist die Stelle, die brechen kann.
import {
  readSqlConsoleEntries,
  sqlConsoleStorageKeys,
  writeSqlConsoleEntries,
} from './sqlConsoleStorage'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/client', () => ({
  api: vi.fn(async () => ({})),
  clearCsrfTokenMemory: vi.fn(),
  getCsrfToken: vi.fn(() => null),
}))

describe('Speicher der SQL-Konsole', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('trennt die Schlüssel nach Benutzer und Konsole', () => {
    const benutzerEins = sqlConsoleStorageKeys(1, 'server-1')
    const benutzerZwei = sqlConsoleStorageKeys(2, 'server-1')
    const panel = sqlConsoleStorageKeys(1, 'panel')

    expect(new Set([benutzerEins.history, benutzerZwei.history, panel.history]).size).toBe(3)

    writeSqlConsoleEntries(benutzerEins.history, ['SELECT email FROM users'])
    expect(readSqlConsoleEntries<string>(benutzerZwei.history)).toEqual([])
    expect(readSqlConsoleEntries<string>(panel.history)).toEqual([])
  })

  it('räumt beim Abmelden Verlauf und Altlasten weg, behält aber Favoriten und Fremdes', async () => {
    const schluessel = sqlConsoleStorageKeys(1, 'server-1')
    // Altlast aus der Zeit vor der Benutzerbindung:
    localStorage.setItem('msm_sql_history', JSON.stringify(['SELECT totp_secret FROM users']))
    localStorage.setItem('msm_sql_favorites', JSON.stringify([]))
    writeSqlConsoleEntries(schluessel.history, ["UPDATE users SET password = 'geheim'"])
    writeSqlConsoleEntries(schluessel.favorites, [
      { id: '1', title: 'Zählen', sql: 'SELECT count(*) FROM users', createdAt: '2026-08-11T00:00:00Z' },
    ])
    localStorage.setItem('theme', 'dark')

    await useAuthStore.getState().logout()

    expect(localStorage.getItem('msm_sql_history')).toBeNull()
    expect(localStorage.getItem('msm_sql_favorites')).toBeNull()
    expect(localStorage.getItem(schluessel.history)).toBeNull()
    expect(readSqlConsoleEntries(schluessel.favorites)).toHaveLength(1)
    expect(localStorage.getItem('theme')).toBe('dark')
  })

  it('nimmt kaputten Inhalt als leere Liste hin', () => {
    const schluessel = sqlConsoleStorageKeys(1, 'panel')
    localStorage.setItem(schluessel.history, '{kein json')
    expect(readSqlConsoleEntries<string>(schluessel.history)).toEqual([])

    localStorage.setItem(schluessel.favorites, '"kein array"')
    expect(readSqlConsoleEntries(schluessel.favorites)).toEqual([])
  })
})
