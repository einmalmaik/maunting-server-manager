import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Abgleichzahl, Zahlenwahl } from './Zahlenabgleich'

describe('Zahlenabgleich', () => {
  it('Abgleichzahl zeigt die Zahl und meldet das Warten als Status', () => {
    render(<Abgleichzahl zahl={47} warteText="Warte auf den Browser" />)
    expect(screen.getByText('47')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Warte auf den Browser')
  })

  it('Zahlenwahl meldet die angetippte Zahl und ist als Gruppe beschriftet', () => {
    const onWaehlen = vi.fn()
    render(<Zahlenwahl zahlen={[12, 47, 83]} onWaehlen={onWaehlen} label="Zahl aus der App wählen" />)
    expect(screen.getByRole('group', { name: 'Zahl aus der App wählen' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '47' }))
    expect(onWaehlen).toHaveBeenCalledWith(47)
  })

  it('Zahlenwahl sperrt alle Knöpfe, solange gesendet wird', () => {
    render(<Zahlenwahl zahlen={[12, 47, 83]} onWaehlen={vi.fn()} label="Wahl" disabled />)
    for (const knopf of screen.getAllByRole('button')) expect(knopf).toBeDisabled()
  })
})
