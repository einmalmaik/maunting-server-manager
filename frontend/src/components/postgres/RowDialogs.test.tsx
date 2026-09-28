/**
 * Nullbare Spalten ohne Standardwert starteten auf „weglassen" — und das sperrte
 * ihr Feld. Im Browser: JSON in `profil` eingetragen, gespeichert, in der Tabelle
 * stand NULL. Wer tippt, meint einen Wert.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RowDialog } from './RowDialogs'
import type { StudioColumn, StudioTableDetails } from './studioApi'
import i18n from '@/i18n'

const insertRow = vi.fn(async () => ({}))

vi.mock('./StudioContext', () => ({
  useStudio: () => ({ api: { insertRow } }),
}))

vi.mock('@/stores/toastStore', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

function spalte(name: string, type: string, extra: Partial<StudioColumn> = {}): StudioColumn {
  return { position: 1, name, type, nullable: true, default: null, identity: null, generated: false, comment: null, category: 'U', enum_values: null, ...extra }
}

const TABELLE = {
  schema: 'public',
  table: 'kunden',
  columns: [
    spalte('id', 'bigint', { identity: 'always', nullable: false }),
    spalte('created_at', 'timestamp with time zone', { default: 'now()', nullable: false }),
    spalte('name', 'text', { nullable: false }),
    spalte('profil', 'jsonb'),
    spalte('notiz', 'text'),
  ],
} as unknown as StudioTableDetails

describe('RowDialog', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    insertRow.mockClear()
  })

  it('schreibt Getipptes auch in nullbare Spalten, lässt Unberührtes weg', async () => {
    render(<RowDialog table={TABELLE} mode="insert" initial={{}} onClose={vi.fn()} onSaved={vi.fn()} />)

    const profil = screen.getByLabelText('profil')
    expect(profil).toBeEnabled()
    fireEvent.change(screen.getByLabelText('name'), { target: { value: 'Anna' } })
    fireEvent.change(profil, { target: { value: '{"plan": "pro"}' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.save') }))

    await waitFor(() => expect(insertRow).toHaveBeenCalled())
    expect(insertRow).toHaveBeenCalledWith('public', 'kunden', { name: 'Anna', profil: { plan: 'pro' } })
  })
})
