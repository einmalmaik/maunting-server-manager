import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { Suchleiste } = await import('./Suchleiste')
const { useSuche } = await import('../services/suche')
const { useTabsStore } = await import('../services/tabsStore')

function treffer(aktuell: number, anzahl: number) {
  useTabsStore.setState((s) => ({
    tabs: s.tabs.map((t) => (t.id === s.aktivId ? { ...t, nativDa: true, treffer: { aktuell, anzahl } } : t)),
  }))
}

describe('Suchleiste', () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    act(() => useSuche.getState().oeffnen())
  })

  // Die WebView2 meldet den aktiven Treffer ab 1; bis 10/2026 stand nach
  // dem ersten Treffer „2 von 2“.
  it('zählt den aktiven Treffer so, wie die WebView2 ihn meldet', () => {
    render(<Suchleiste />)
    fireEvent.change(screen.getByRole('textbox', { name: 'In der Seite suchen' }), { target: { value: 'domain' } })
    act(() => treffer(1, 2))
    expect(screen.getByText('1 von 2')).toBeInTheDocument()
    act(() => treffer(2, 2))
    expect(screen.getByText('2 von 2')).toBeInTheDocument()
    act(() => treffer(0, 0))
    expect(screen.getByText('0 von 0')).toBeInTheDocument()
  })
})
