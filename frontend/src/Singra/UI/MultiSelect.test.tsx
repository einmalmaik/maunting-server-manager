import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MultiSelect } from './MultiSelect'

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
})
