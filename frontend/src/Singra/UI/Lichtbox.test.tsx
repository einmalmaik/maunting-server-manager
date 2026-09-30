import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { Lichtbox } from './Lichtbox'

function Aufbau({ onZoom, video = false }: { onZoom?: () => void; video?: boolean }) {
  const bilder = ['eins', 'zwei', 'drei']
  const [index, setIndex] = useState<number | null>(null)
  return (
    <>
      <button type="button" onClick={() => setIndex(0)}>
        öffnen
      </button>
      {index !== null && (
        <Lichtbox
          kennung={bilder[index]}
          titel={bilder[index]}
          position={{ index, anzahl: bilder.length }}
          onSchliessen={() => setIndex(null)}
          onVor={index < bilder.length - 1 ? () => setIndex(index + 1) : undefined}
          onZurueck={index > 0 ? () => setIndex(index - 1) : undefined}
          zoombar
          onZoom={onZoom}
          info={<p>Details zu {bilder[index]}</p>}
        >
          {video ? <video data-testid="inhalt" controls /> : <div data-testid="inhalt">{bilder[index]}</div>}
        </Lichtbox>
      )}
    </>
  )
}

const titel = () => screen.getByRole('dialog').getAttribute('aria-label')

function wischen(ziel: Element, dx: number, dy = 0) {
  fireEvent.pointerDown(ziel, { button: 0, pointerId: 1, clientX: 200, clientY: 200 })
  fireEvent.pointerUp(ziel, { button: 0, pointerId: 1, clientX: 200 + dx, clientY: 200 + dy })
}

describe('Lichtbox', () => {
  it('blättert mit Pfeiltasten, schließt mit Escape und gibt den Fokus zurück', () => {
    render(<Aufbau />)
    const knopf = screen.getByText('öffnen')
    knopf.focus()
    fireEvent.click(knopf)
    expect(titel()).toBe('eins')
    expect(screen.getByText('1 von 3')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(titel()).toBe('eins')
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(titel()).toBe('drei')

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(knopf)
  })

  it('blättert per Wischen, aber nicht bei senkrechten oder kurzen Bewegungen', () => {
    render(<Aufbau />)
    fireEvent.click(screen.getByText('öffnen'))
    const flaeche = screen.getByTestId('inhalt')
    wischen(flaeche, -30)
    wischen(flaeche, -80, 120)
    expect(titel()).toBe('eins')
    wischen(flaeche, -100)
    expect(titel()).toBe('zwei')
    wischen(flaeche, 100)
    expect(titel()).toBe('eins')
  })

  it('nimmt Spulen in einem Video nicht als Wischen', () => {
    render(<Aufbau video />)
    fireEvent.click(screen.getByText('öffnen'))
    wischen(screen.getByTestId('inhalt'), -150)
    expect(titel()).toBe('eins')
  })

  it('meldet das erste Hineinzoomen einmal, wischt gezoomt nicht und setzt den Zoom beim Blättern zurück', () => {
    const onZoom = vi.fn()
    render(<Aufbau onZoom={onZoom} />)
    fireEvent.click(screen.getByText('öffnen'))
    const flaeche = screen.getByTestId('inhalt')
    fireEvent.doubleClick(flaeche)
    fireEvent.wheel(flaeche, { deltaY: -100 })
    expect(onZoom).toHaveBeenCalledTimes(1)
    expect(flaeche.parentElement!.style.transform).toContain('scale(3)')

    wischen(flaeche, -150)
    expect(titel()).toBe('eins')

    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(titel()).toBe('zwei')
    expect(screen.getByTestId('inhalt').parentElement!.style.transform).toContain('scale(1)')
    fireEvent.doubleClick(screen.getByTestId('inhalt'))
    expect(onZoom).toHaveBeenCalledTimes(2)
  })

  it('zeigt die Info-Leiste erst auf Wunsch', () => {
    render(<Aufbau />)
    fireEvent.click(screen.getByText('öffnen'))
    expect(screen.queryByText('Details zu eins')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Informationen' }))
    expect(screen.getByText('Details zu eins')).toBeInTheDocument()
  })
})
