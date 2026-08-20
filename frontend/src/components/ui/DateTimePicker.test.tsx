import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DateTimePicker } from './DateTimePicker'

describe('DateTimePicker', () => {
  it('renders trigger with placeholder when value is empty', () => {
    render(<DateTimePicker value="" onChange={() => {}} placeholder="Zeitpunkt wählen" />)
    expect(screen.getByRole('button')).toHaveTextContent('Zeitpunkt wählen')
  })

  it('renders formatted date time when value is provided', () => {
    render(<DateTimePicker value="2026-08-21T14:30" onChange={() => {}} />)
    expect(screen.getByRole('button')).toHaveTextContent(/21\.08\.2026.*14:30/)
  })

  it('opens calendar dialog on click and selects a date', () => {
    const handleChange = vi.fn()
    render(<DateTimePicker value="2026-08-21T14:30" onChange={handleChange} />)

    const trigger = screen.getByRole('button')
    fireEvent.click(trigger)

    expect(screen.getByRole('dialog')).toBeInTheDocument()

    // Click on day 15
    const day15 = screen.getByRole('button', { name: '15' })
    fireEvent.click(day15)

    expect(handleChange).toHaveBeenCalledWith('2026-08-15T14:30')
  })

  it('navigates months with previous and next buttons', () => {
    render(<DateTimePicker value="2026-08-21T14:30" onChange={() => {}} />)

    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText(/August 2026/i)).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Vorheriger Monat'))
    expect(screen.getByText(/Juli 2026/i)).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Nächster Monat'))
    expect(screen.getByText(/August 2026/i)).toBeInTheDocument()
  })

  it('closes dialog on Escape key', () => {
    render(<DateTimePicker value="2026-08-21T14:30" onChange={() => {}} />)

    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
