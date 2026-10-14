import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useToastStore } from '@/stores/toastStore'

import { Meldungsleiste } from './Meldungsleiste'

describe('Meldungsleiste', () => {
  beforeEach(() => {
    useToastStore.getState().clearAll()
  })

  it('zeigt nichts ohne Meldungen', () => {
    const { container } = render(<Meldungsleiste />)
    expect(container).toBeEmptyDOMElement()
  })

  it('zeichnet Fehler als Alert mit Plakette und Maschinentext', () => {
    useToastStore.getState().addToast('TypeError: Failed to fetch', 'error')
    render(<Meldungsleiste />)
    const meldung = screen.getByRole('alert')
    expect(meldung).toHaveTextContent('TypeError: Failed to fetch')
    expect(meldung.querySelector('p')).toHaveClass('font-mono')
  })

  it('zeichnet Hinweise als Status ohne Maschinentext', () => {
    useToastStore.getState().addToast('Gespeichert.', 'success')
    render(<Meldungsleiste />)
    const meldung = screen.getByRole('status')
    expect(meldung).toHaveTextContent('Gespeichert.')
    expect(meldung.querySelector('p')).not.toHaveClass('font-mono')
  })

  it('führt die Aktion aus und räumt die Meldung ab', () => {
    const ausfuehren = vi.fn()
    useToastStore.getState().addToast('Update bereit.', 'info', { label: 'Neu laden', ausfuehren })
    render(<Meldungsleiste />)
    fireEvent.click(screen.getByRole('button', { name: 'Neu laden' }))
    expect(ausfuehren).toHaveBeenCalledTimes(1)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('schließt per Knopf mit barrierefreiem Namen', () => {
    useToastStore.getState().addToast('Hinweis.', 'warning')
    render(<Meldungsleiste />)
    const meldung = screen.getByRole('status')
    fireEvent.click(within(meldung).getByRole('button', { name: 'Schließen' }))
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
