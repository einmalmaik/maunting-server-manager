/**
 * Die Panel-Datenbank öffnet dasselbe Studio wie eine Server-Datenbank — mit
 * Admin-Recht, aber ohne Strukturwerkzeuge (die Struktur gehört den
 * Migrationen) und ohne Sicherung und Verbindung (Panel-Backups, Installation).
 * Die Server-Fassung mit denselben Rechten ist die Gegenprobe.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PostgresStudio } from './PostgresStudio'
import type { StudioOverview, StudioZiel } from './studioApi'
import i18n from '@/i18n'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'

const aufrufe: string[] = []
let art: StudioOverview['kind'] = 'panel'

vi.mock('@/api/client', () => ({
  api: vi.fn(async (pfad: string) => {
    aufrufe.push(pfad)
    if (pfad.endsWith('/studio')) {
      return {
        kind: art,
        server: { version_num: 170000, version: '17.0', role: 'msm', database: 'msm', size_bytes: 1 },
        schemas: [{ name: 'public', owner: 'msm', can_create: true, table_count: 0 }],
        permissions: { read: true, write: true, admin: true },
      } satisfies StudioOverview
    }
    if (pfad.endsWith('/studio/sql')) return { results: [], notices: [], duration_ms: 1, rolled_back: false }
    if (pfad.includes('/studio/objects')) return { schema: 'public', relations: [], sequences: [], enums: [], functions: [], triggers: [] }
    return []
  }),
}))

async function oeffne(ziel: StudioZiel) {
  render(<PostgresStudio ziel={ziel} />)
  await screen.findByText(i18n.t('postgresStudio.explorer.nothingSelected'))
}

describe('PostgresStudio', () => {
  beforeEach(async () => {
    aufrufe.length = 0
    await i18n.changeLanguage('de')
  })

  it('zeigt der Panel-Datenbank Daten und SQL, aber keine Strukturwerkzeuge', async () => {
    art = 'panel'
    await oeffne({ art: 'panel' })

    expect(aufrufe[0]).toBe('/panel/database/studio')
    expect(screen.getByTestId('studio-panel-hint')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: i18n.t('postgresStudio.tabs.sql') })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: i18n.t('postgresStudio.tabs.backup') })).toBeNull()
    expect(screen.queryByRole('tab', { name: i18n.t('postgresStudio.tabs.connection') })).toBeNull()
    expect(screen.queryByRole('button', { name: i18n.t('postgresStudio.designer.title') })).toBeNull()
  })

  it('zeigt einer Server-Datenbank mit denselben Rechten alles', async () => {
    art = 'shared'
    await oeffne({ art: 'server', serverId: 3, databaseId: 7 })

    expect(aufrufe[0]).toBe('/servers/3/databases/7/studio')
    expect(screen.queryByTestId('studio-panel-hint')).toBeNull()
    expect(screen.getByRole('tab', { name: i18n.t('postgresStudio.tabs.backup') })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: i18n.t('postgresStudio.tabs.connection') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('postgresStudio.designer.title') })).toBeInTheDocument()
  })

  it('merkt den SQL-Verlauf unter dem Konto, nicht unter dem Rechner', async () => {
    // Bis 27.09.2026 lag der Verlauf unter msm-pg-studio:verlauf:panel, ohne
    // Konto, und das Abmelden räumte ihn nicht weg: der nächste Benutzer am
    // Rechner sah die Abfragen des vorigen, samt Passwort aus CREATE ROLE.
    localStorage.clear()
    setzeAngemeldetesKonto(5)
    art = 'panel'
    await oeffne({ art: 'panel' })

    fireEvent.click(screen.getByRole('tab', { name: i18n.t('postgresStudio.tabs.sql') }))
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('postgresStudio.sql.run') }))

    await waitFor(() => expect(localStorage.getItem('msm_sql:history:5:panel')).toContain('SELECT now();'))
    expect(Object.keys(localStorage).filter(key => key.startsWith('msm-pg-studio:'))).toEqual([])
    setzeAngemeldetesKonto(null)
  })
})
