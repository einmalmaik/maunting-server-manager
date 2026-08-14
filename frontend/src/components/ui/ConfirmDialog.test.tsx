import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ConfirmDialog } from './ConfirmDialog'
import { confirm, useConfirmStore } from '@/stores/confirmStore'

/** Der Fokus ist die einzige Zusage, die dieser Dialog gegenüber der Tastatur
 * macht: er wandert beim Öffnen hinein und beim Schließen zum Auslöser zurück.
 */
describe('ConfirmDialog', () => {
  afterEach(() => {
    act(() => {
      useConfirmStore.setState({ pending: null })
    })
  })

  const oeffnen = () => {
    render(
      <div>
        <button data-testid="ausloeser">Löschen</button>
        <ConfirmDialog />
      </div>,
    )
    const ausloeser = screen.getByTestId('ausloeser')
    ausloeser.focus()
    expect(document.activeElement).toBe(ausloeser)

    act(() => {
      void confirm({ message: 'Wirklich löschen?', confirmText: 'Ja', cancelText: 'Nein' })
    })
    return ausloeser
  }

  it('gibt den Fokus nach dem Bestätigen an den Auslöser zurück', () => {
    const ausloeser = oeffnen()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Ja' }))

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

  it('lässt den Fokus in Ruhe, wenn der Auslöser inzwischen verschwunden ist', () => {
    const ausloeser = oeffnen()
    ausloeser.remove()

    fireEvent.click(screen.getByRole('button', { name: 'Nein' }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(document.body)
  })
})
