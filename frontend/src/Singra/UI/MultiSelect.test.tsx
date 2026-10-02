import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MultiSelect } from './MultiSelect'
import { fakeLayout } from '@/test/fakeLayout'

const options = [
  { value: 'user', label: 'User' },
  { value: 'vip', label: 'AI-VIP' },
  { value: 'admin', label: 'Admin', disabled: true },
]

describe('MultiSelect', () => {
  it('adds and removes options as a stable ordered set', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <MultiSelect
        aria-label="Rollen zuweisen"
        options={options}
        values={['vip']}
        onChange={onChange}
        placeholder="Keine Rolle"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Rollen zuweisen' }))
    fireEvent.click(screen.getByRole('option', { name: 'User' }))
    expect(onChange).toHaveBeenLastCalledWith(['user', 'vip'])

    rerender(
      <MultiSelect
        aria-label="Rollen zuweisen"
        options={options}
        values={['user', 'vip']}
        onChange={onChange}
        placeholder="Keine Rolle"
      />,
    )
    fireEvent.click(screen.getByRole('option', { name: 'AI-VIP' }))
    expect(onChange).toHaveBeenLastCalledWith(['user'])
  })

  it('marks disabled options and closes on Escape', () => {
    render(
      <MultiSelect
        aria-label="Rollen zuweisen"
        options={options}
        values={[]}
        onChange={vi.fn()}
        placeholder="Keine Rolle"
      />,
    )

    const trigger = screen.getByRole('button', { name: 'Rollen zuweisen' })
    fireEvent.click(trigger)
    expect(screen.getByRole('option', { name: 'Admin' })).toBeDisabled()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('klappt am unteren Rand nach oben und hängt an body', () => {
    // Bis 02.10.2026 hing die Liste immer unter dem Feld, im Stapelkontext der Seite.
    const layout = fakeLayout({
      fenster: { breite: 1024, hoehe: 768 },
      anker: { left: 100, top: 700, width: 240, height: 40 },
      popover: { width: 240, height: 200 },
    })
    try {
      render(<MultiSelect aria-label="Rollen zuweisen" options={options} values={[]} onChange={vi.fn()} placeholder="Keine Rolle" />)
      fireEvent.click(screen.getByRole('button', { name: 'Rollen zuweisen' }))
      const liste = screen.getByRole('listbox')
      expect(liste.parentElement).toBe(document.body)
      expect(liste.style.top).toBe(`${700 - 8 - 200}px`)
      expect(liste.style.minWidth).toBe('240px')
      expect(layout.imFenster(liste)).toBe(true)
    } finally {
      layout.aufraeumen()
    }
  })

  it('wählt per Klick in die Liste, ohne dass sie sich schließt', () => {
    const onChange = vi.fn()
    render(<MultiSelect aria-label="Rollen zuweisen" options={options} values={[]} onChange={onChange} placeholder="Keine Rolle" />)
    fireEvent.click(screen.getByRole('button', { name: 'Rollen zuweisen' }))
    const user = screen.getByRole('option', { name: 'User' })
    fireEvent.pointerDown(user)
    fireEvent.click(user)
    expect(onChange).toHaveBeenLastCalledWith(['user'])
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('wandert mit den Pfeiltasten durch die Liste', () => {
    render(<MultiSelect aria-label="Rollen zuweisen" options={options} values={[]} onChange={vi.fn()} placeholder="Keine Rolle" />)
    fireEvent.click(screen.getByRole('button', { name: 'Rollen zuweisen' }))
    const user = screen.getByRole('option', { name: 'User' })
    user.focus()
    fireEvent.keyDown(user, { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: 'AI-VIP' })).toHaveFocus()
  })
})
