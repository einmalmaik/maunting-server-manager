/**
 * Eingaben in Hostlisten (Jugendschutz, „Immer wach“). Beide Tests waren am
 * Stand 229b690d rot (Bugjagd 08.10.2026).
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ausnahmeHost } from '../services/leistung'
import { Hostliste } from './Hostliste'

describe('Hostliste', () => {
  it('nimmt eine Domain mit Umlaut ohne https:// genauso wie mit', () => {
    expect(ausnahmeHost('https://bücher.de')).toBe('xn--bcher-kva.de')
    expect(ausnahmeHost('bücher.de')).toBe('xn--bcher-kva.de')
    expect(ausnahmeHost('www.Example.com/pfad')).toBe('example.com')
    expect(ausnahmeHost('kein host')).toBeNull()
  })

  it('sagt, wenn die Liste voll ist, statt den Eintrag still zu verwerfen', () => {
    const setzen = vi.fn()
    const voll = Array.from({ length: 500 }, (_, i) => `s${i}.example`)
    render(<Hostliste titel="Eigene Sperren" hinweis="" eintraege={voll} setzen={setzen} max={500} />)
    const feld = screen.getByRole('textbox')
    fireEvent.change(feld, { target: { value: 'neu.example' } })
    fireEvent.submit(feld.closest('form')!)
    expect(setzen).not.toHaveBeenCalled()
    expect(feld).toHaveValue('neu.example')
    expect(screen.getByText('Die Liste ist voll (500 Einträge). Entferne erst einen.')).toBeInTheDocument()
  })
})
