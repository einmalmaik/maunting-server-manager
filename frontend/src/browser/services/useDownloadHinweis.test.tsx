import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'

import { useDownloadsStore } from './downloadsStore'
import { useDownloadHinweis } from './useDownloadHinweis'

function Probe() {
  useDownloadHinweis()
  const navigate = useNavigate()
  return <><span data-testid="ort">{useLocation().pathname}</span><button onClick={() => navigate('/')}>Schließen</button></>
}

const melden = (nr: number, stand: 'start' | 'pruefung' | 'fertig' | 'fehler' = 'start') =>
  useDownloadsStore.getState().ereignis({ art: 'download', id: 'geschlossenes-popup', nr, stand, url: 'https://example.com/datei.exe', datei: 'datei.exe' })

const zeigen = () => render(<MemoryRouter initialEntries={['/verlauf']}><Probe /></MemoryRouter>)

describe('Sichtbares Download-Feedback', () => {
  beforeEach(() => useDownloadsStore.setState({ downloads: [], neu: 0 }))

  it('öffnet die Liste sofort beim neuen Download, auch ohne offenen Tab', () => {
    zeigen()
    act(() => melden(1))
    expect(screen.getByTestId('ort')).toHaveTextContent('/downloads')
  })

  it('öffnet auch eine sofortige Fehlermeldung statt den Klick stumm zu lassen', () => {
    zeigen()
    act(() => melden(1, 'fehler'))
    expect(screen.getByTestId('ort')).toHaveTextContent('/downloads')
  })

  it('lässt die geschlossene Liste bei Fortschritt und Abschluss zu, aber öffnet den nächsten Download', () => {
    zeigen()
    act(() => melden(1))
    fireEvent.click(screen.getByRole('button', { name: 'Schließen' }))
    act(() => {
      useDownloadsStore.getState().ereignis({ art: 'download_fortschritt', id: 'geschlossenes-popup', nr: 1, empfangen: 50, gesamt: 100, bytes_pro_sekunde: 10, rest_sekunden: 5, ohne_daten_sekunden: 0 })
      melden(1, 'pruefung')
      melden(1, 'fertig')
    })
    expect(screen.getByTestId('ort').textContent).toBe('/')
    act(() => melden(2))
    expect(screen.getByTestId('ort')).toHaveTextContent('/downloads')
  })

  it('öffnet alte Einträge beim Einhängen nicht erneut und meldet sich beim Entfernen ab', () => {
    melden(1)
    const { unmount } = zeigen()
    expect(screen.getByTestId('ort')).toHaveTextContent('/verlauf')
    unmount()
    act(() => melden(2))
    zeigen()
    expect(screen.getByTestId('ort')).toHaveTextContent('/verlauf')
  })
})
