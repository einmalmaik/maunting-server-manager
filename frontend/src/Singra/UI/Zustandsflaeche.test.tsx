import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Zustandsflaeche } from './Zustandsflaeche'

describe('Zustandsflaeche', () => {
  it('meldet einen Fehler als Alarm und lädt über den Knopf neu', () => {
    const onErneut = vi.fn()
    render(<Zustandsflaeche art="fehler" text="Ging nicht" erneutLabel="Erneut laden" onErneut={onErneut} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Ging nicht')
    fireEvent.click(screen.getByRole('button', { name: 'Erneut laden' }))
    expect(onErneut).toHaveBeenCalledTimes(1)
  })

  it('sagt „offline“ als Status und ohne Knopf: geladen wird von selbst', () => {
    render(<Zustandsflaeche art="offline" titel="Ohne Netz" text="Kommt, sobald das Netz da ist" erneutLabel="Erneut laden" onErneut={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent('Ohne Netz')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('ist leer still, nach einer Suche angesagt, und nimmt eine eigene Handlung auf', () => {
    const { rerender } = render(
      <Zustandsflaeche art="leer" titel="Noch nichts da">
        <button type="button">Hochladen</button>
      </Zustandsflaeche>,
    )
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('button', { name: 'Hochladen' })).toBeInTheDocument()

    rerender(<Zustandsflaeche art="leer" ansagen text="Keine Treffer" />)
    expect(screen.getByRole('status')).toHaveTextContent('Keine Treffer')
  })
})
