import { act, fireEvent, render, screen } from '@testing-library/react'
import { useRef, useState } from 'react'
import { describe, expect, it } from 'vitest'
import { ankerLage, useAnkerLage } from './Ankerlage'
import { fakeLayout } from '@/test/fakeLayout'

const fenster = { breite: 1000, hoehe: 800 }
const knopf = { left: 100, right: 160, top: 100, bottom: 132 }

describe('ankerLage', () => {
  it('liegt unter dem Auslöser, links bündig, solange Platz ist', () => {
    expect(ankerLage(knopf, 200, 150, fenster, { abstand: 8 })).toEqual({ left: 100, top: 140 })
  })

  it('richtet am Ende rechts bündig aus und legt sich auf Wunsch darüber', () => {
    const unten = { left: 700, right: 760, top: 600, bottom: 632 }
    expect(ankerLage(unten, 200, 150, fenster, { abstand: 8, seite: 'oben', ausrichtung: 'ende' })).toEqual({ left: 560, top: 442 })
  })

  it('klappt um, wenn die gewünschte Seite nicht reicht und die andere mehr Platz hat', () => {
    const rechtsUnten = { left: 900, right: 960, top: 720, bottom: 752 }
    expect(ankerLage(rechtsUnten, 200, 150, fenster, { abstand: 8 })).toEqual({ left: 760, top: 562 })
    const linksOben = { left: 20, right: 80, top: 20, bottom: 52 }
    expect(ankerLage(linksOben, 200, 150, fenster, { abstand: 8, seite: 'oben', ausrichtung: 'ende' })).toEqual({ left: 20, top: 60 })
  })

  it('bleibt im Fenster, auch wenn keine Seite reicht', () => {
    expect(ankerLage(knopf, 1200, 900, fenster, { abstand: 8 })).toEqual({ left: 8, top: 8 })
  })
})

function Probe() {
  const [offen, setOffen] = useState(false)
  const anker = useRef<HTMLButtonElement>(null)
  const popover = useRef<HTMLDivElement>(null)
  const stil = useAnkerLage(offen, anker, popover)
  return (
    <div data-testid="rollflaeche">
      <button ref={anker} type="button" onClick={() => setOffen(true)}>
        Öffnen
      </button>
      {offen && <div ref={popover} role="menu" style={stil} />}
    </div>
  )
}

describe('useAnkerLage', () => {
  it('folgt dem Auslöser bei Fenstergröße und beim Scrollen in einem inneren Bereich', () => {
    const layout = fakeLayout({
      fenster: { breite: 1000, hoehe: 800 },
      anker: { left: 100, top: 100, width: 60, height: 32 },
      popover: { width: 200, height: 150 },
    })
    try {
      render(<Probe />)
      fireEvent.click(screen.getByRole('button', { name: 'Öffnen' }))
      const menue = screen.getByRole('menu')
      expect(menue.style.top).toBe('140px')
      expect(menue.style.visibility).toBe('')

      layout.ankerNach({ left: 100, top: 300, width: 60, height: 32 })
      act(() => {
        window.dispatchEvent(new Event('resize'))
      })
      expect(menue.style.top).toBe('340px')

      // Scrollen in einer Liste meldet sich nicht am Fenster, nur in der Capture-Phase.
      layout.ankerNach({ left: 100, top: 700, width: 60, height: 32 })
      act(() => {
        screen.getByTestId('rollflaeche').dispatchEvent(new Event('scroll'))
      })
      expect(menue.style.top).toBe(`${700 - 8 - 150}px`)
    } finally {
      layout.aufraeumen()
    }
  })
})
