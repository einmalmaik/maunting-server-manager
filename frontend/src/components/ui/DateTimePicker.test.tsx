import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DateTimePicker } from './DateTimePicker'
import { fakeLayout } from '@/test/fakeLayout'

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

  describe('dateOnly: ein Feld, dem eine Uhrzeit nichts nuetzt', () => {
    it('gibt nur das Datum zurueck, ohne Uhrzeit-Anhang', () => {
      const handleChange = vi.fn()
      render(<DateTimePicker value="2026-08-21" onChange={handleChange} dateOnly />)

      fireEvent.click(screen.getByRole('button'))
      fireEvent.click(screen.getByRole('button', { name: '15' }))

      expect(handleChange).toHaveBeenCalledWith('2026-08-15')
    })

    it('zeigt weder Stunden- noch Minutenfeld', () => {
      render(<DateTimePicker value="2026-08-21" onChange={() => {}} dateOnly />)

      fireEvent.click(screen.getByRole('button'))
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      // Ein Bedienelement, dessen Eingabe weggeworfen wird, darf es nicht geben.
      expect(screen.queryByText('Stunde')).toBeNull()
      expect(screen.queryByText('Minute')).toBeNull()
    })

    it('laesst die Uhrzeit stehen, solange dateOnly fehlt', () => {
      const handleChange = vi.fn()
      render(<DateTimePicker value="2026-08-21T14:30" onChange={handleChange} />)

      fireEvent.click(screen.getByRole('button'))
      expect(screen.getByText('Stunde')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: '15' }))

      expect(handleChange).toHaveBeenCalledWith('2026-08-15T14:30')
    })
  })

  it('bleibt in einem niedrigen Fenster ganz sichtbar', () => {
    // Bis 02.10.2026 rechnete der Kalender mit fest 360 px und ragte bei 600 px
    // Fensterhöhe unten 66 px hinaus, weil oben noch weniger Platz war.
    const layout = fakeLayout({
      fenster: { breite: 1024, hoehe: 600 },
      anker: { left: 100, top: 250, width: 200, height: 40 },
      popover: { width: 320, height: 360 },
    })
    try {
      render(<DateTimePicker value="2026-08-21T14:30" onChange={() => {}} />)
      fireEvent.click(screen.getByRole('button'))
      const kalender = screen.getByRole('dialog')
      expect(layout.imFenster(kalender)).toBe(true)
      expect(kalender.style.left).toBe('100px')
    } finally {
      layout.aufraeumen()
    }
  })

  it('klappt am unteren Rand nach oben und am rechten nach links', () => {
    const layout = fakeLayout({
      fenster: { breite: 1024, hoehe: 768 },
      anker: { left: 900, top: 700, width: 110, height: 40 },
      popover: { width: 320, height: 360 },
    })
    try {
      render(<DateTimePicker value="2026-08-21T14:30" onChange={() => {}} />)
      fireEvent.click(screen.getByRole('button'))
      const kalender = screen.getByRole('dialog')
      expect(kalender.style.top).toBe(`${700 - 8 - 360}px`)
      expect(kalender.style.left).toBe(`${1010 - 320}px`)
      expect(layout.imFenster(kalender)).toBe(true)
    } finally {
      layout.aufraeumen()
    }
  })
})
