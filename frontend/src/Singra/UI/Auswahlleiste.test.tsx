import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Auswahlleiste, type AuswahlAktion } from './Auswahlleiste'

const aktionen = (loeschen = vi.fn()): AuswahlAktion[] => [
  { key: 'v', label: 'Verschieben', icon: null, onSelect: vi.fn() },
  { key: 'l', label: 'Löschen', icon: null, onSelect: loeschen, destructive: true },
  { key: 'g', label: 'Gesperrt', icon: null, onSelect: vi.fn(), disabled: true },
]

describe('Auswahlleiste', () => {
  it('nennt die Anzahl, bricht ab und wählt alle', () => {
    const abbrechen = vi.fn()
    const alle = vi.fn()
    render(<Auswahlleiste variante="kopf" anzahlLabel="3 ausgewählt" aktionen={aktionen()} abbrechenLabel="Aufheben" onAbbrechen={abbrechen} alleLabel="Alle" onAlle={alle} />)
    expect(screen.getByRole('toolbar', { name: '3 ausgewählt' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Aufheben' }))
    fireEvent.click(screen.getByRole('button', { name: 'Alle' }))
    expect(abbrechen).toHaveBeenCalledTimes(1)
    expect(alle).toHaveBeenCalledTimes(1)
  })

  it('gibt Aufheben und Alle in der Kopfleiste am Finger 44 px', () => {
    // Im Emulator waren beide bis 02.10.2026 32 px hoch.
    render(<Auswahlleiste variante="kopf" anzahlLabel="1 ausgewählt" aktionen={aktionen()} abbrechenLabel="Aufheben" onAbbrechen={vi.fn()} alleLabel="Alle" onAlle={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Aufheben' }).className).toMatch(/max-md:h-11 max-md:w-11/)
    expect(screen.getByRole('button', { name: 'Alle' }).className).toMatch(/max-md:h-11/)
  })

  it('führt in der Fußleiste die Aktion aus und sperrt deaktivierte', () => {
    const loeschen = vi.fn()
    render(<Auswahlleiste variante="fuss" anzahlLabel="2 ausgewählt" aktionen={aktionen(loeschen)} abbrechenLabel="Aufheben" onAbbrechen={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Löschen' }))
    expect(loeschen).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Gesperrt' })).toBeDisabled()
    // Abbrechen steht nur oben; unten zählt Daumenreichweite für die Aktionen.
    expect(screen.queryByRole('button', { name: 'Aufheben' })).not.toBeInTheDocument()
  })

  it('zeigt unten die Kurzform und behält den vollen Namen', () => {
    const aktion: AuswahlAktion = { key: 's', label: 'Auf dem Gerät speichern', kurz: 'Speichern', icon: null, onSelect: vi.fn() }
    render(<Auswahlleiste variante="fuss" anzahlLabel="1 ausgewählt" aktionen={[aktion]} abbrechenLabel="Aufheben" onAbbrechen={() => {}} />)
    const knopf = screen.getByRole('button', { name: 'Auf dem Gerät speichern' })
    expect(knopf).toHaveTextContent('Speichern')
    expect(knopf).not.toHaveTextContent('Gerät')
  })
})
