import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ConfirmDialog } from './ConfirmDialog'
import { Dialog, DialogContent } from '@/Singra/UI/Dialog'
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

  it('bricht mit der Zurück-Taste ab, statt die App zu verlassen', async () => {
    render(<ConfirmDialog />)
    let antwort: Promise<boolean> = Promise.resolve(true)
    act(() => {
      antwort = confirm({ message: 'Wirklich löschen?', confirmText: 'Ja', cancelText: 'Nein' })
    })
    await waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await expect(antwort).resolves.toBe(false)
    await waitFor(() => expect(screen.queryByText('Wirklich löschen?')).not.toBeInTheDocument())
  })

  it('liegt über einem offenen Vollbild-Dialog, nicht dahinter', () => {
    render(
      <div>
        <ConfirmDialog />
        <Dialog open onOpenChange={() => undefined}>
          <DialogContent>Editor</DialogContent>
        </Dialog>
      </div>,
    )
    act(() => {
      void confirm({ message: 'Ungesichertes verwerfen?' })
    })
    const rueckfrage = screen.getByText('Ungesichertes verwerfen?').closest('.msm-modal-overlay')!
    const editor = screen.getByText('Editor').closest('.msm-modal-overlay')!
    // Gleiche Ebene (z-50): oben liegt, was später im DOM steht.
    expect(rueckfrage.parentElement).toBe(document.body)
    expect(editor.compareDocumentPosition(rueckfrage) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
  it('hat einen Namen und eine Beschreibung für Screenreader', () => {
    render(<ConfirmDialog />)
    act(() => {
      void confirm({ message: 'Wirklich löschen?' })
    })
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Wirklich löschen?')
    act(() => useConfirmStore.setState({ pending: null }))
    act(() => {
      void confirm({ title: 'Datei löschen', message: 'Wirklich löschen?' })
    })
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Datei löschen')
    expect(screen.getByRole('dialog')).toHaveAccessibleDescription('Wirklich löschen?')
  })

  it('hält Tab im Dialog', () => {
    oeffnen()
    const ja = screen.getByRole('button', { name: 'Ja' })
    ja.focus()
    // fireEvent gibt false zurück, wenn preventDefault gerufen wurde.
    expect(fireEvent.keyDown(ja, { key: 'Tab' })).toBe(false)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Nein' }))
    expect(fireEvent.keyDown(document.activeElement!, { key: 'Tab', shiftKey: true })).toBe(false)
    expect(document.activeElement).toBe(ja)
  })
})
