import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Blatteintrag, Blattknopf } from './Blattmenue'

describe('Blattknopf', () => {
  it('trägt seine Beschriftung und sagt, dass ein Blatt daran hängt', () => {
    render(
      <Blattknopf label="Weitere Einstellungen" titel="Chat">
        <Blatteintrag label="Stummschalten" onClick={() => undefined} />
      </Blattknopf>,
    )

    const knopf = screen.getByRole('button', { name: 'Weitere Einstellungen' })
    expect(knopf).toHaveAttribute('aria-haspopup', 'dialog')
    expect(knopf).toHaveAttribute('aria-expanded', 'false')
    // Geschlossen ist das Blatt nicht gezeichnet, nicht nur unsichtbar.
    expect(screen.queryByText('Stummschalten')).not.toBeInTheDocument()
  })

  it('öffnet das Blatt und gibt der Zeile das Schliessen in die Hand', () => {
    const gerufen = vi.fn()
    render(
      <Blattknopf label="Mehr" titel="Chat" ueberschrift="msgtest-anna">
        {(schliessen) => (
          <Blatteintrag
            label="Hintergrund"
            onClick={() => {
              schliessen()
              gerufen()
            }}
          />
        )}
      </Blattknopf>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Mehr' }))
    expect(screen.getByText('msgtest-anna')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mehr' })).toHaveAttribute('aria-expanded', 'true')

    fireEvent.click(screen.getByText('Hintergrund'))

    expect(gerufen).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Hintergrund')).not.toBeInTheDocument()
  })

  it('schliesst bei Escape und beim Klick daneben', () => {
    render(
      <Blattknopf label="Mehr" titel="Chat">
        <Blatteintrag label="Löschen" gefahr onClick={() => undefined} />
      </Blattknopf>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Mehr' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText('Löschen')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Mehr' }))
    fireEvent.click(screen.getByRole('dialog'))
    expect(screen.queryByText('Löschen')).not.toBeInTheDocument()
  })

  it('bleibt zu, solange er abgeschaltet ist', () => {
    render(
      <Blattknopf label="Mehr" titel="Chat" disabled>
        <Blatteintrag label="Stummschalten" onClick={() => undefined} />
      </Blattknopf>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Mehr' }))
    expect(screen.queryByText('Stummschalten')).not.toBeInTheDocument()
  })

  it('lässt eine abgeschaltete Zeile nichts auslösen', () => {
    const gerufen = vi.fn()
    render(
      <Blattknopf label="Mehr" titel="Chat">
        <Blatteintrag label="Verlauf löschen" disabled onClick={gerufen} />
      </Blattknopf>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Mehr' }))
    fireEvent.click(screen.getByText('Verlauf löschen'))
    expect(gerufen).not.toHaveBeenCalled()
  })
})
