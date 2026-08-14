import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { PromptDialog } from './PromptDialog'
import { prompt, usePromptStore } from '@/stores/promptStore'

/** Der Fokus ist die einzige Zusage, die dieser Dialog gegenüber der Tastatur
 * macht: er wandert beim Öffnen ins Eingabefeld und beim Schließen zum
 * Auslöser zurück.
 */
describe('PromptDialog', () => {
  afterEach(() => {
    act(() => {
      usePromptStore.setState({ pending: null })
    })
  })

  const oeffnen = () => {
    render(
      <div>
        <button data-testid="ausloeser">Umbenennen</button>
        <PromptDialog />
      </div>,
    )
    const ausloeser = screen.getByTestId('ausloeser')
    ausloeser.focus()
    expect(document.activeElement).toBe(ausloeser)

    act(() => {
      void prompt({ message: 'Neuer Name?', confirmText: 'Ja', cancelText: 'Nein' })
    })
    return ausloeser
  }

  it('gibt den Fokus nach dem Bestätigen an den Auslöser zurück', () => {
    const ausloeser = oeffnen()
    expect(document.activeElement).toBe(screen.getByRole('textbox'))

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'neuer-name' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ja' }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(ausloeser)
  })

  it('gibt den Fokus auch nach Escape zurück', () => {
    const ausloeser = oeffnen()

    fireEvent.keyDown(window, { key: 'Escape' })

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(ausloeser)
  })

  it('behält den Fokus im Eingabefeld, während getippt wird', () => {
    oeffnen()
    const feld = screen.getByRole('textbox')

    fireEvent.change(feld, { target: { value: 'ab' } })

    expect(document.activeElement).toBe(feld)
  })
})
