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
})
