import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bestaetigenMock = vi.fn()
const ablehnenMock = vi.fn()
const ergebnisMeldenMock = vi.fn()
let ereignisRuf: ((e: { payload: unknown }) => void) | null = null

vi.mock('@tauri-apps/api/event', () => ({
  listen: (_name: string, rueckruf: (e: { payload: unknown }) => void) => {
    ereignisRuf = rueckruf
    return Promise.resolve(() => {})
  },
}))

vi.mock('./tauri', () => ({
  desktopAktionBestaetigen: (...args: unknown[]) => bestaetigenMock(...args),
  desktopAktionAblehnen: (...args: unknown[]) => ablehnenMock(...args),
}))

vi.mock('./desktopJobs', () => ({
  ergebnisMelden: (...args: unknown[]) => ergebnisMeldenMock(...args),
}))

import { DesktopAktionKarte } from './DesktopAktionKarte'

describe('DesktopAktionKarte', () => {
  beforeEach(() => {
    ereignisRuf = null
    bestaetigenMock.mockReset().mockResolvedValue({ status: 'ok' })
    ablehnenMock.mockReset().mockResolvedValue(undefined)
    ergebnisMeldenMock.mockReset().mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('zeigt ohne Ereignis nichts an', () => {
    const { container } = render(<DesktopAktionKarte offenerAuftragId={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('zeigt Bestätigungskarte bei Ereignis an und führt bei Klick auf Ja aus', async () => {
    render(<DesktopAktionKarte offenerAuftragId="fallback-id" />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())

    ereignisRuf!({
      payload: {
        auftrag_id: 'job-screen-123',
        werkzeug: 'desktop_system',
        titel: 'Bildschirmaufnahme (Screenshot)',
        beschreibung: 'Die KI möchte ein Bild deines Hauptbildschirms aufnehmen.',
        argumente: { aktion: 'bildschirm' },
      },
    })

    expect(await screen.findByText('Bildschirmaufnahme (Screenshot)')).toBeInTheDocument()
    expect(
      screen.getByText('Die KI möchte ein Bild deines Hauptbildschirms aufnehmen.'),
    ).toBeInTheDocument()

    const jaBtn = screen.getByRole('button', { name: /mss\.aktion\.bestaetigen|Ja, ausführen/i })
    fireEvent.click(jaBtn)

    await waitFor(() => expect(bestaetigenMock).toHaveBeenCalledWith('job-screen-123'))
    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledWith(
      'job-screen-123',
      true,
      { status: 'ok' },
    ))
  })

  it('lehnt Aktion bei Klick auf Nein ab und meldet Rejection', async () => {
    render(<DesktopAktionKarte offenerAuftragId="fallback-id" />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())

    ereignisRuf!({
      payload: {
        auftrag_id: 'job-app-456',
        werkzeug: 'desktop_launch_app',
        titel: 'Programm starten',
        beschreibung: 'Die KI möchte das Programm Steam starten.',
        argumente: { programm: 'Steam' },
      },
    })

    const neinBtn = await screen.findByRole('button', { name: /mss\.aktion\.ablehnen|Nein, ablehnen/i })
    fireEvent.click(neinBtn)

    await waitFor(() => expect(ablehnenMock).toHaveBeenCalledWith('job-app-456'))
    await waitFor(() =>
      expect(ergebnisMeldenMock).toHaveBeenCalledWith(
        'job-app-456',
        false,
        expect.objectContaining({ abgewiesen: true }),
        'DESKTOP_ACTION_REJECTED',
      ),
    )
  })
})
