import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const gerufen: { befehl: string; args: unknown }[] = []

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: unknown) => {
    gerufen.push({ befehl, args })
    return Promise.resolve(null)
  },
}))

const { SeitenDialoge } = await import('./SeitenDialoge')
const { KLICKSPERRE_MS } = await import('./klickSperre')
const { useRueckfragen } = await import('../services/rueckfragen')
const { useTabsStore } = await import('../services/tabsStore')

const vorne = useTabsStore.getState().aktivId!

function frage(nr: number, id = vorne) {
  return { art: 'dialog' as const, id, nr, dialog: 'prompt' as const, herkunft: 'https://beispiel.de/seite', text: 'Name?', vorgabe: 'Ada' }
}

// Die Knöpfe nehmen erst nach `KLICKSPERRE_MS` einen Klick an (`klickSperre.ts`).
let jetzt = 0
const spaeter = () => {
  jetzt += KLICKSPERRE_MS
}

describe('Fragen einer Seite', () => {
  beforeEach(() => {
    jetzt = 1_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => jetzt)
    gerufen.length = 0
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    useRueckfragen.setState({ offen: [] })
  })

  it('nennt die Herkunft und gibt die Eingabe des Prompts an die Seite', async () => {
    act(() => useRueckfragen.getState().aufnehmen(frage(7)))
    render(<SeitenDialoge />)
    expect(screen.getByRole('dialog', { name: 'beispiel.de meldet' })).toBeInTheDocument()
    const feld = screen.getByRole('textbox', { name: 'Antwort an die Seite' })
    expect(feld).toHaveValue('Ada')
    fireEvent.change(feld, { target: { value: 'Grace' } })
    spaeter()
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))
    await act(() => Promise.resolve())
    expect(gerufen).toContainEqual({ befehl: 'tab_antworten', args: { nr: 7, antwort: { art: 'dialog', ok: true, text: 'Grace' } } })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('lehnt mit Escape ab', async () => {
    act(() => useRueckfragen.getState().aufnehmen(frage(8)))
    render(<SeitenDialoge />)
    fireEvent.keyDown(document, { key: 'Escape' })
    await act(() => Promise.resolve())
    expect(gerufen).toContainEqual({ befehl: 'tab_antworten', args: { nr: 8, antwort: { art: 'dialog', ok: false, text: null } } })
  })

  it('fragt nach Rechten mit dem Fokus auf „Blockieren“ und nimmt einen Klick erst nach einer halben Sekunde an', async () => {
    act(() => useRueckfragen.getState().aufnehmen({ art: 'recht', id: vorne, nr: 10, recht: 'kamera', herkunft: 'https://beispiel.de/' }))
    render(<SeitenDialoge />)
    expect(screen.getByRole('button', { name: 'Blockieren' })).toHaveFocus()
    // Ein Klick oder Enter, der eigentlich der Seite galt, erlaubt nichts.
    fireEvent.click(screen.getByRole('button', { name: 'Erlauben' }))
    await act(() => Promise.resolve())
    expect(gerufen.filter((g) => g.befehl === 'tab_antworten')).toEqual([])
    jetzt += KLICKSPERRE_MS - 1
    fireEvent.click(screen.getByRole('button', { name: 'Erlauben' }))
    await act(() => Promise.resolve())
    expect(gerufen.filter((g) => g.befehl === 'tab_antworten')).toEqual([])
    jetzt += 1
    fireEvent.click(screen.getByRole('button', { name: 'Erlauben' }))
    await act(() => Promise.resolve())
    expect(gerufen).toContainEqual({ befehl: 'tab_antworten', args: { nr: 10, antwort: { art: 'recht', erlauben: true } } })
  })

  it('zeigt nicht, was ein Tab im Hintergrund fragt', () => {
    act(() => useRueckfragen.getState().aufnehmen(frage(9, 'tab-hinten')))
    render(<SeitenDialoge />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
