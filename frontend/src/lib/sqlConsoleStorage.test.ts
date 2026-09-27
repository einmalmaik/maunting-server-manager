import { beforeEach, describe, expect, it, vi } from 'vitest'

// `clearSqlConsoleHistory` wird hier bewusst nicht direkt eingebunden: der
// Test unten ruft `logout()` und prueft damit zugleich, dass der authStore die
// Aufraeumfunktion ueberhaupt aufruft. Ein Direktaufruf haette genau diese
// Verdrahtung nicht mitgeprueft — und sie ist die Stelle, die brechen kann.
import {
  readSqlConsoleEntries,
  sqlVerlaufSchluessel,
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

  it('trennt die Schlüssel nach Benutzer und Datenbank', () => {
    const benutzerEins = sqlVerlaufSchluessel(1, 'server-1-db-1')
    const benutzerZwei = sqlVerlaufSchluessel(2, 'server-1-db-1')
    const panel = sqlVerlaufSchluessel(1, 'panel')

    expect(new Set([benutzerEins, benutzerZwei, panel]).size).toBe(3)

    writeSqlConsoleEntries(benutzerEins, ['SELECT email FROM users'])
    expect(readSqlConsoleEntries(benutzerZwei)).toEqual([])
    expect(readSqlConsoleEntries(panel)).toEqual([])
  })

  it('räumt beim Abmelden Verlauf und Altlasten weg, Fremdes bleibt', async () => {
    const schluessel = sqlVerlaufSchluessel(1, 'server-1-db-1')
    // Altlasten: ungebunden aus der Zeit vor der Benutzerbindung, Favoriten
    // der alten Konsole und der Studio-Verlauf unter seinem alten Namen.
    localStorage.setItem('msm_sql_history', JSON.stringify(['SELECT totp_secret FROM users']))
    localStorage.setItem('msm_sql:favorites:1:panel', JSON.stringify([]))
    localStorage.setItem('msm-pg-studio:verlauf:panel', JSON.stringify(["CREATE ROLE app PASSWORD 'geheim'"]))
    writeSqlConsoleEntries(schluessel, ["UPDATE users SET password = 'geheim'"])
    localStorage.setItem('theme', 'dark')

    await useAuthStore.getState().logout()

    expect(localStorage.getItem('msm_sql_history')).toBeNull()
    expect(localStorage.getItem('msm_sql:favorites:1:panel')).toBeNull()
    expect(localStorage.getItem('msm-pg-studio:verlauf:panel')).toBeNull()
    expect(localStorage.getItem(schluessel)).toBeNull()
    expect(localStorage.getItem('theme')).toBe('dark')
  })

  it('nimmt kaputten Inhalt als leere Liste hin', () => {
    const schluessel = sqlVerlaufSchluessel(1, 'panel')
    localStorage.setItem(schluessel, '{kein json')
    expect(readSqlConsoleEntries(schluessel)).toEqual([])

    localStorage.setItem(schluessel, '"kein array"')
    expect(readSqlConsoleEntries(schluessel)).toEqual([])
  })
})
