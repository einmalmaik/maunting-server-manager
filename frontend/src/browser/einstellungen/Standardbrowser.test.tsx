/**
 * Standardbrowser unter Android: Festlegen fragt Android; fragt es nicht
 * mehr (zweimal abgelehnt), bleibt der Weg über die Android-Einstellungen.
 * Unter Windows öffnet Festlegen die Standard-Apps des Browsers, und der
 * Stand wird neu gelesen, wenn das Fenster den Fokus zurückbekommt.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { Standardbrowser } from './Standardbrowser'

vi.mock('../services/plattform', () => ({ istAndroid: vi.fn(() => true) }))

describe('Standardbrowser', () => {
  const stand = vi.spyOn(nativ, 'standardbrowser')
  const werden = vi.spyOn(nativ, 'standardbrowserWerden')
  const einstellungen = vi.spyOn(nativ, 'standardbrowserEinstellungen')

  beforeEach(() => {
    stand.mockReset()
    werden.mockReset()
    einstellungen.mockReset()
    vi.mocked(istAndroid).mockReturnValue(true)
  })

  it('sagt, wenn Links aus anderen Apps schon hier öffnen, und bietet nichts an', async () => {
    stand.mockResolvedValue(true)
    render(<Standardbrowser />)
    expect(await screen.findByText('Links aus anderen Apps öffnen hier.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('fragt Android und zeigt danach den Weg über die Einstellungen, wenn es nicht geklappt hat', async () => {
    stand.mockResolvedValue(false)
    werden.mockResolvedValue(false)
    einstellungen.mockResolvedValue(false)
    render(<Standardbrowser />)
    fireEvent.click(await screen.findByRole('button', { name: 'Als Standard festlegen' }))
    expect(werden).toHaveBeenCalledTimes(1)
    fireEvent.click(await screen.findByRole('button', { name: 'Android-Einstellungen öffnen' }))
    expect(einstellungen).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/Nach zweimal „Nein“/)).toBeInTheDocument()
  })

  it('liest den Stand neu, wenn man aus den Android-Einstellungen zurückkommt', async () => {
    stand.mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    render(<Standardbrowser />)
    await screen.findByRole('button', { name: 'Als Standard festlegen' })
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() => expect(screen.queryByRole('button')).toBeNull())
    expect(screen.getByText('Links aus anderen Apps öffnen hier.')).toBeInTheDocument()
  })

  it('öffnet unter Windows die Standard-Apps und liest den Stand beim Zurückkommen neu', async () => {
    vi.mocked(istAndroid).mockReturnValue(false)
    stand.mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    werden.mockResolvedValue(false)
    render(<Standardbrowser />)
    expect(await screen.findByText(/Links aus Mail, Teams und anderen Programmen/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'In Windows festlegen' }))
    expect(werden).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(/„Als Standard festlegen“ klicken/)).toBeInTheDocument()
    // Einen zweiten Weg wie unter Android gibt es nicht.
    expect(screen.queryByRole('button', { name: 'Android-Einstellungen öffnen' })).toBeNull()
    window.dispatchEvent(new Event('focus'))
    expect(await screen.findByText('Links aus anderen Programmen öffnen hier.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
