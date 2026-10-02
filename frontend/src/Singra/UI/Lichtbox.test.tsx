import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { Lichtbox } from './Lichtbox'
import { Dialog, DialogContent } from './Dialog'
import { Blattmenue } from './Blattmenue'

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

  it('zoomt mit zwei Fingern, hält die Mitte zwischen ihnen und bleibt in den Grenzen', () => {
    const onZoom = vi.fn()
    render(<Aufbau onZoom={onZoom} />)
    fireEvent.click(screen.getByText('öffnen'))
    const flaeche = screen.getByTestId('inhalt')
    const huelle = () => screen.getByTestId('inhalt').parentElement!.style.transform

    // jsdom misst die Fläche mit 0 × 0: ihr Mittelpunkt liegt bei (0, 0).
    fireEvent.pointerDown(flaeche, { button: 0, pointerId: 1, isPrimary: true, clientX: 100, clientY: 100 })
    fireEvent.pointerDown(flaeche, { button: 0, pointerId: 2, isPrimary: false, clientX: 200, clientY: 100 })
    fireEvent.pointerMove(flaeche, { pointerId: 2, clientX: 300, clientY: 100 })
    // Abstand 100 → 200: doppelt. Der Punkt (150, 100) unter den Fingern liegt jetzt bei (200, 100).
    expect(huelle()).toBe('translate(-100px, -100px) scale(2)')
    expect(onZoom).toHaveBeenCalledTimes(1)

    fireEvent.pointerMove(flaeche, { pointerId: 2, clientX: 2000, clientY: 100 })
    expect(huelle()).toContain('scale(5)')
    fireEvent.pointerMove(flaeche, { pointerId: 2, clientX: 110, clientY: 100 })
    expect(huelle()).toBe('translate(0px, 0px) scale(1)')

    // Nach dem Spreizen blättert der übrige Finger nicht, auch bei weitem Zug.
    fireEvent.pointerUp(flaeche, { button: 0, pointerId: 2, clientX: 110, clientY: 100 })
    fireEvent.pointerMove(flaeche, { pointerId: 1, clientX: -100, clientY: 100 })
    fireEvent.pointerUp(flaeche, { button: 0, pointerId: 1, clientX: -100, clientY: 100 })
    expect(titel()).toBe('eins')

    // Einfaches Wischen geht danach wie vorher.
    wischen(flaeche, -100)
    expect(titel()).toBe('zwei')
  })

  it('verschiebt nach dem Spreizen mit dem übrigen Finger', () => {
    render(<Aufbau />)
    fireEvent.click(screen.getByText('öffnen'))
    const flaeche = screen.getByTestId('inhalt')
    fireEvent.pointerDown(flaeche, { button: 0, pointerId: 1, isPrimary: true, clientX: -50, clientY: 0 })
    fireEvent.pointerDown(flaeche, { button: 0, pointerId: 2, clientX: 50, clientY: 0 })
    fireEvent.pointerMove(flaeche, { pointerId: 2, clientX: 150, clientY: 0 })
    fireEvent.pointerUp(flaeche, { button: 0, pointerId: 2, clientX: 150, clientY: 0 })
    const vorher = screen.getByTestId('inhalt').parentElement!.style.transform
    expect(vorher).toContain('scale(2)')
    fireEvent.pointerMove(flaeche, { pointerId: 1, clientX: -20, clientY: 40 })
    expect(screen.getByTestId('inhalt').parentElement!.style.transform).not.toBe(vorher)
    expect(screen.getByTestId('inhalt').parentElement!.style.transform).toContain('scale(2)')
  })

  it('zoomt mit +, - und 0, lässt Strg+Plus aber dem Browser', () => {
    const onZoom = vi.fn()
    render(<Aufbau onZoom={onZoom} />)
    fireEvent.click(screen.getByText('öffnen'))
    const transform = () => screen.getByTestId('inhalt').parentElement!.style.transform

    fireEvent.keyDown(document, { key: '+' })
    expect(transform()).toContain('scale(1.2)')
    expect(onZoom).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document, { key: '+' })
    expect(transform()).toContain('scale(1.44)')
    fireEvent.keyDown(document, { key: '-' })
    expect(transform()).toContain('scale(1.2)')
    for (let i = 0; i < 30; i++) fireEvent.keyDown(document, { key: '+' })
    expect(transform()).toContain('scale(5)')
    fireEvent.keyDown(document, { key: '0' })
    expect(transform()).toBe('translate(0px, 0px) scale(1)')

    const browser = fireEvent.keyDown(document, { key: '+', ctrlKey: true })
    expect(browser).toBe(true)
    expect(transform()).toContain('scale(1)')
  })

  it('macht die Knöpfe der Kopfleiste auf dem Telefon 44 px groß, auch die des Aufrufers', () => {
    render(
      <Lichtbox kennung="a" titel="a" onSchliessen={() => {}} aktionen={<button type="button">Teilen</button>}>
        <div>Bild</div>
      </Lichtbox>,
    )
    const kopf = screen.getByRole('button', { name: 'Teilen' }).closest('header')!
    expect(kopf.className).toContain('max-sm:[&_button]:min-h-11')
    expect(kopf.className).toContain('max-sm:[&_button]:min-w-11')
    // Deckend und über dem Bild, sonst scheint darunter die App durch.
    expect(kopf.className).toMatch(/(^| )bg-black( |$)/)
    expect(kopf.className).toContain('z-10')
  })

  it('zeigt die Info-Leiste erst auf Wunsch', () => {
    render(<Aufbau />)
    fireEvent.click(screen.getByText('öffnen'))
    expect(screen.queryByText('Details zu eins')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Informationen' }))
    expect(screen.getByText('Details zu eins')).toBeInTheDocument()
  })

  it('lässt rollbaren Inhalt die Fläche füllen, ohne Zoom und ohne Gestensperre', () => {
    render(
      <Lichtbox kennung="pdf" titel="pdf" onSchliessen={() => {}} rollbar>
        <div data-testid="inhalt">Seiten</div>
      </Lichtbox>,
    )
    const huelle = screen.getByTestId('inhalt').parentElement!
    expect(huelle.style.transform).toBe('')
    expect(huelle.className).toContain('h-full')
    expect(huelle.parentElement!.className).not.toContain('touch-none')
  })

  it('schließt mit der Zurück-Taste', async () => {
    // Ein Vortest kann beim Aufräumen noch einen Schritt zurück laufen haben.
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    render(<Aufbau />)
    fireEvent.click(screen.getByText('öffnen'))
    await waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

describe('Lichtbox unter einem Overlay', () => {
  // Bis 02.10.2026 hörte die Lichtbox jede Taste: Escape schloss eine Rückfrage
  // und die Lichtbox darunter, ArrowRight blätterte hinter dem Dialog weiter.
  function Gestapelt({ oben }: { oben: 'dialog' | 'blatt' }) {
    const bilder = ['eins', 'zwei']
    const [index, setIndex] = useState<number | null>(0)
    const [ueber, setUeber] = useState(false)
    return (
      <>
        {index !== null && (
          <Lichtbox
            kennung={bilder[index]}
            titel={bilder[index]}
            onSchliessen={() => setIndex(null)}
            onVor={index < bilder.length - 1 ? () => setIndex(index + 1) : undefined}
            aktionen={
              <button type="button" onClick={() => setUeber(true)}>
                darüber
              </button>
            }
          >
            <div>{bilder[index]}</div>
          </Lichtbox>
        )}
        {oben === 'dialog' ? (
          <Dialog open={ueber} onOpenChange={setUeber}>
            <DialogContent aria-label="Rückfrage">
              <button type="button">ok</button>
            </DialogContent>
          </Dialog>
        ) : (
          <Blattmenue offen={ueber} onSchliessen={() => setUeber(false)} titel="Blatt">
            <button type="button">ok</button>
          </Blattmenue>
        )}
      </>
    )
  }

  it.each(['dialog', 'blatt'] as const)('überlässt Escape und Pfeile dem %s darüber', async (oben) => {
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    render(<Gestapelt oben={oben} />)
    fireEvent.click(screen.getByText('darüber'))
    const ueberName = oben === 'dialog' ? 'Rückfrage' : 'Blatt'
    expect(screen.getByRole('dialog', { name: ueberName })).toBeInTheDocument()

    fireEvent.keyDown(screen.getByText('ok'), { key: 'ArrowRight' })
    expect(screen.getByRole('dialog', { name: 'eins' })).toBeInTheDocument()

    fireEvent.keyDown(screen.getByText('ok'), { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: ueberName })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'eins' })).toBeInTheDocument()
  })
})
