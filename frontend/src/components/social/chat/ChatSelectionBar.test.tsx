import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { ChatSelectionBar } from './ChatSelectionBar'

const handlungen = () => ({
  onBeenden: vi.fn(),
  onWeiterleiten: vi.fn(),
  onKopieren: vi.fn(),
  onMarkieren: vi.fn(),
  onLoeschen: vi.fn(),
})

describe('ChatSelectionBar', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de')
  })

  // Bis 02.10.2026 stand „ausgewählt“, „Weiterleiten“, „Kopieren“ und
  // „Markieren“ fest im Code, auch in der englischen App.
  it('spricht die Sprache der App', async () => {
    await i18n.changeLanguage('en')
    render(<ChatSelectionBar anzahl={2} loeschenMoeglich {...handlungen()} />)
    expect(screen.getAllByRole('toolbar', { name: '2 selected' })).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: 'Forward' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: 'Copy text' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: 'Mark' }).length).toBeGreaterThan(0)
    expect(screen.queryByText(/ausgewählt|Weiterleiten|Kopieren|Markieren/)).toBeNull()
  })

  it('sperrt Löschen ohne eigene Nachricht und beendet über Abbrechen', () => {
    const h = handlungen()
    render(<ChatSelectionBar anzahl={1} loeschenMoeglich={false} {...h} />)
    for (const knopf of screen.getAllByRole('button', { name: i18n.t('common.delete') })) expect(knopf).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('messenger.endSelection') }))
    expect(h.onBeenden).toHaveBeenCalledTimes(1)
  })
})
