import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Dialog, DialogContent } from './Dialog'

function Aufbau({ escapeSchliesst }: { escapeSchliesst?: boolean }) {
  const [offen, setOffen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOffen(true)}>
        öffnen
      </button>
      <Dialog open={offen} onOpenChange={setOffen} escapeSchliesst={escapeSchliesst}>
        <DialogContent>Inhalt</DialogContent>
      </Dialog>
    </>
  )
}

describe('Dialog', () => {
  it('schließt mit der Zurück-Taste und lässt danach keinen Eintrag liegen', async () => {
    render(<Aufbau />)
    fireEvent.click(screen.getByText('öffnen'))
    await waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await waitFor(() => expect(screen.queryByText('Inhalt')).not.toBeInTheDocument())
    expect(window.history.state?.msmTiefe ?? 0).toBe(0)
  })

  it('lässt Escape dem Inhalt, wenn escapeSchliesst aus ist; Zurück schließt trotzdem', async () => {
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    render(<Aufbau escapeSchliesst={false} />)
    fireEvent.click(screen.getByText('öffnen'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByText('Inhalt')).toBeInTheDocument()
    await waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await waitFor(() => expect(screen.queryByText('Inhalt')).not.toBeInTheDocument())
  })

  it('schließt mit Escape nur den obersten von zwei offenen Dialogen', async () => {
    // Über dem Bearbeiten-Dialog des Tresors liegt der QR-Scanner. Bis
    // 02.10.2026 schloss Escape beide, und das Getippte darunter war weg.
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    function Gestapelt() {
      const [unten, setUnten] = useState(true)
      const [oben, setOben] = useState(true)
      return (
        <>
          <Dialog open={unten} onOpenChange={setUnten}>
            <DialogContent>Unten</DialogContent>
          </Dialog>
          <Dialog open={oben} onOpenChange={setOben}>
            <DialogContent>Oben</DialogContent>
          </Dialog>
        </>
      )
    }
    render(<Gestapelt />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText('Oben')).not.toBeInTheDocument()
    expect(screen.getByText('Unten')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText('Unten')).not.toBeInTheDocument()
  })
  it('springt beim Tippen ins zweite Feld nicht ins erste zurück', async () => {
    // Der Wert des Providers war bei jedem Neuzeichnen neu, und der Effekt hing
    // daran: der Fokus sprang an den Auslöser und dann aufs erste Feld.
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    function Formular() {
      const [offen, setOffen] = useState(false)
      const [eins, setEins] = useState('')
      const [zwei, setZwei] = useState('')
      return (
        <>
          <button type="button" onClick={() => setOffen(true)}>
            öffnen
          </button>
          <Dialog open={offen} onOpenChange={setOffen}>
            <DialogContent>
              <input aria-label="eins" value={eins} onChange={(e) => setEins(e.target.value)} />
              <input aria-label="zwei" value={zwei} onChange={(e) => setZwei(e.target.value)} />
            </DialogContent>
          </Dialog>
        </>
      )
    }
    render(<Formular />)
    const knopf = screen.getByText('öffnen')
    knopf.focus()
    fireEvent.click(knopf)
    const zwei = screen.getByLabelText('zwei')
    zwei.focus()
    fireEvent.change(zwei, { target: { value: 'a' } })
    expect(zwei).toHaveValue('a')
    expect(document.activeElement).toBe(zwei)
  })
})
