import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useUebersetzung, type Eintrag } from './ablauf'
import { Leisteninhalt } from './Leisteninhalt'

// Die Klicksperre neuer Leisten prüft `FormularLeiste.test.tsx`; hier wird sofort geklickt.
vi.mock('../seite/klickSperre', () => ({ useKlickSperre: () => () => {} }))

const eintrag = (stand: Eintrag['stand'], von = 'fr'): Eintrag => ({ url: 'https://example.org/', von, nach: 'de', stand, lauf: 1 })

describe('Übersetzungsleiste', () => {
  it('nennt vor dem ersten Laden die Größe und lädt erst auf Klick', () => {
    const starten = vi.spyOn(useUebersetzung.getState(), 'starten').mockResolvedValue()
    render(<Leisteninhalt tab="tab-a" e={eintrag({ art: 'wahl', fehlt: 36_719_532 })} />)
    expect(screen.getByText(/einmalig 35\.0 MB Sprachdaten von Mozilla/)).toBeTruthy()
    expect(starten).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Laden und übersetzen' }))
    expect(starten).toHaveBeenCalledWith('tab-a')
  })

  it('lässt ohne bekannte Sprache nicht übersetzen', () => {
    render(<Leisteninhalt tab="tab-a" e={eintrag({ art: 'wahl', fehlt: null }, '')} />)
    expect(screen.getByText('Wähle die Sprache der Seite und die Zielsprache.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Übersetzen' }).hasAttribute('disabled')).toBe(true)
  })

  it('bietet nach dem Übersetzen das Original an', () => {
    const original = vi.spyOn(useUebersetzung.getState(), 'original').mockImplementation(() => {})
    render(<Leisteninhalt tab="tab-a" e={eintrag({ art: 'fertig' })} />)
    expect(screen.getByText('Von Französisch nach Deutsch übersetzt')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Original anzeigen' }))
    expect(original).toHaveBeenCalledWith('tab-a')
  })

  it('nennt den Grund eines Fehlers und schließt mit eigenem Namen', () => {
    const schliessen = vi.spyOn(useUebersetzung.getState(), 'schliessen').mockImplementation(() => {})
    render(<Leisteninhalt tab="tab-a" e={eintrag({ art: 'fehler', grund: 'pruefsumme' })} />)
    expect(screen.getByText(/passen nicht zur erwarteten Fassung/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Übersetzung schließen' }))
    expect(schliessen).toHaveBeenCalledWith('tab-a')
  })
})
