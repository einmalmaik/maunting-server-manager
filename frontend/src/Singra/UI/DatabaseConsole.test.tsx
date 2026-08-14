import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { DatabaseConsole, buildRowKeyConditions, type DatabaseConsoleProps } from './DatabaseConsole'
import { useAuthStore } from '@/stores/authStore'
import type { PostgresTableInfo } from '@/types'

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

describe('DatabaseConsole — Dialoge am Tastenbrett', () => {
  beforeEach(() => {
    localStorage.clear()
    meldeAn(1)
  })

  it('kündigt den Favoritendialog als Dialog an', () => {
    oeffneKonsole()
    fireEvent.click(screen.getByRole('button', { name: 'Favorit speichern' }))

    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true')
  })

  it('schließt den Favoritendialog mit Escape und gibt den Fokus zurück', () => {
    oeffneKonsole()
    const ausloeser = screen.getByRole('button', { name: 'Favorit speichern' })
    ausloeser.focus()
    fireEvent.click(ausloeser)
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(ausloeser)
  })
})

/**
 * Die Bedingung geht ungefiltert als WHERE-Klausel in ein `DELETE` ohne `LIMIT`
 * und in ein `UPDATE`. Eine unvollständige Bedingung trifft dort fremde Zeilen.
 *
 * Der frühere Rückfallzweig ließ NULL- und JSON-Spalten stillschweigend weg:
 * bei `spieler_log(name, notiz)` mit ('anna', NULL) und ('anna', 'gebannt')
 * entstand für die erste Zeile `{name: 'anna'}` — und damit
 * `DELETE FROM spieler_log WHERE name = 'anna'`, das beide Zeilen löscht.
 */
describe('buildRowKeyConditions', () => {
  function tabelle(spalten: Array<{ name: string; primary_key?: boolean }>): PostgresTableInfo {
    return {
      schema: 'public',
      name: 'spieler_log',
      columns: spalten.map((s) => ({
        name: s.name,
        data_type: 'text',
        nullable: true,
        primary_key: s.primary_key ?? false,
      })),
      indexes: [],
      foreign_keys: [],
    }
  }

  it('nimmt den Primärschlüssel und nur ihn', () => {
    const info = tabelle([{ name: 'id', primary_key: true }, { name: 'name' }])
    expect(buildRowKeyConditions({ id: 7, name: 'anna' }, info, ['id', 'name'])).toEqual({ id: 7 })
  })

  it('sperrt eine Zeile ohne Primärschlüssel, sobald eine Spalte NULL ist', () => {
    const info = tabelle([{ name: 'name' }, { name: 'notiz' }])
    expect(buildRowKeyConditions({ name: 'anna', notiz: null }, info, ['name', 'notiz'])).toBeNull()
  })

  it('sperrt eine Zeile, deren Spalte JSON enthält — `=` vergleicht das nicht', () => {
    const info = tabelle([{ name: 'name' }, { name: 'daten' }])
    expect(buildRowKeyConditions({ name: 'anna', daten: { a: 1 } }, info, ['name', 'daten'])).toBeNull()
  })

  it('beschreibt eine Zeile ohne Primärschlüssel über alle Spalten, wenn keine fehlt', () => {
    const info = tabelle([{ name: 'name' }, { name: 'notiz' }])
    expect(buildRowKeyConditions({ name: 'anna', notiz: 'gebannt' }, info, ['name', 'notiz'])).toEqual({
      name: 'anna',
      notiz: 'gebannt',
    })
  })

  it('sperrt eine Zeile ohne jede Spalte, statt eine leere Bedingung zu liefern', () => {
    expect(buildRowKeyConditions({}, null, [])).toBeNull()
  })
})
