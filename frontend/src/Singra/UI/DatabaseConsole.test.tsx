import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DatabaseConsole, type DatabaseConsoleProps } from './DatabaseConsole'
import { useAuthStore } from '@/stores/authStore'

// Der Store zieht den API-Client mit; die Konsole ruft im Test nichts davon auf.
vi.mock('@/api/client', () => ({
  api: vi.fn(),
  clearCsrfTokenMemory: vi.fn(),
  getCsrfToken: vi.fn(() => null),
}))

const ABFRAGE = 'SELECT email, totp_secret FROM users WHERE id=7'

const basisProps: DatabaseConsoleProps = {
  title: 'Datenbanken',
  subtitle: 'Test',
  databases: [{ id: 1, name: 'db', owner_role: 'msm', is_power_user: false }],
  selectedDatabaseId: 1,
  stats: null,
  tables: [],
  selectedTable: null,
  tableInfo: null,
  rows: null,
  sqlText: ABFRAGE,
  sqlResult: null,
  history: [],
  storageScope: 'server-1',
  canAdmin: true,
  onSelectDatabase: () => undefined,
  onSelectTable: () => undefined,
  onSearchRows: () => undefined,
  onSqlTextChange: () => undefined,
  onRunSql: () => undefined,
}

function meldeAn(id: number) {
  useAuthStore.setState({
    user: { id, username: `benutzer${id}` } as any,
    isAuthenticated: true,
    isLoading: false,
  })
}

function oeffneKonsoleUndFuehreAus(props: Partial<DatabaseConsoleProps> = {}) {
  const ansicht = render(<DatabaseConsole {...basisProps} {...props} />)
  fireEvent.click(screen.getByRole('button', { name: 'SQL-Konsole' }))
  fireEvent.click(screen.getByRole('button', { name: 'Ausführen' }))
  return ansicht
}

function oeffneKonsole(props: Partial<DatabaseConsoleProps> = {}) {
  render(<DatabaseConsole {...basisProps} {...props} />)
  fireEvent.click(screen.getByRole('button', { name: 'SQL-Konsole' }))
}

describe('DatabaseConsole — Abfrageverlauf', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('zeigt dem nächsten Benutzer am selben Rechner nicht die Abfragen des vorigen', () => {
    meldeAn(1)
    const ersteSitzung = oeffneKonsoleUndFuehreAus()
    expect(screen.getByTitle(ABFRAGE)).toBeInTheDocument()
    ersteSitzung.unmount()

    // Benutzer 2 meldet sich an. localStorage überlebt das Abmelden — der
    // Verlauf darf trotzdem nicht mitkommen.
    meldeAn(2)
    oeffneKonsole()

    expect(screen.queryByTitle(ABFRAGE)).toBeNull()
    expect(screen.getByText('Noch keine Abfragen im Verlauf.')).toBeInTheDocument()
  })

  it('trägt den Verlauf eines Servers nicht in die Konsole des nächsten', () => {
    meldeAn(1)
    const ersterServer = oeffneKonsoleUndFuehreAus({ storageScope: 'server-1' })
    expect(screen.getByTitle(ABFRAGE)).toBeInTheDocument()
    ersterServer.unmount()

    oeffneKonsole({ storageScope: 'server-2' })

    expect(screen.queryByTitle(ABFRAGE)).toBeNull()
    expect(screen.getByText('Noch keine Abfragen im Verlauf.')).toBeInTheDocument()
  })

  it('behält den eigenen Verlauf desselben Benutzers in derselben Konsole', () => {
    meldeAn(1)
    const sitzung = oeffneKonsoleUndFuehreAus()
    sitzung.unmount()

    oeffneKonsole()

    expect(screen.getByTitle(ABFRAGE)).toBeInTheDocument()
  })
})
