import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bestaetigenMock = vi.fn()
const ablehnenMock = vi.fn()
const ergebnisMeldenMock = vi.fn()
let ereignisRuf: ((e: { payload: unknown }) => void) | null = null
let erledigtRuf: ((e: { payload: unknown }) => void) | null = null
const emitMock = vi.fn()

vi.mock('@tauri-apps/api/event', () => ({
  // `mss:karte-erledigt` hat seinen eigenen Rückruf; `emit` stellt wie der
  // Tauri-Bus an jedes Fenster zu, auch an das sendende.
  listen: (name: string, rueckruf: (e: { payload: unknown }) => void) => {
    if (name === 'mss:karte-erledigt') erledigtRuf = rueckruf
    else ereignisRuf = rueckruf
    return Promise.resolve(() => {})
  },
  emit: (name: string, payload: unknown) => {
    emitMock(name, payload)
    if (name === 'mss:karte-erledigt') erledigtRuf?.({ payload })
    return Promise.resolve()
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
    erledigtRuf = null
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

    const jaBtn = screen.getByRole('button', { name: /(mss\.aktion\.bestaetigen|Ja, ausführen|Yes, execute)/i })
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

    const neinBtn = await screen.findByRole('button', { name: /(mss\.aktion\.ablehnen|Nein, ablehnen|No, reject)/i })
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

  const anfrage = (auftrag_id: string, titel: string) => ({
    payload: {
      auftrag_id,
      werkzeug: 'desktop_steuern',
      titel,
      beschreibung: `Beschreibung ${titel}`,
      argumente: { aktion: 'klick' },
    },
  })

  it('zwei Anfragen warten nacheinander, die ältere zuerst', async () => {
    // Früher gab es einen Platz: die zweite Karte verdrängte die erste, und
    // deren Auftrag lief ins Leere.
    render(<DesktopAktionKarte offenerAuftragId={null} />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())

    ereignisRuf!(anfrage('job-1', 'Erste Aktion'))
    ereignisRuf!(anfrage('job-2', 'Zweite Aktion'))

    expect(await screen.findByText('Erste Aktion')).toBeInTheDocument()
    expect(screen.queryByText('Zweite Aktion')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /(mss\.aktion\.bestaetigen|Ja, ausführen|Yes, execute)/i }))
    await waitFor(() => expect(bestaetigenMock).toHaveBeenCalledWith('job-1'))

    expect(await screen.findByText('Zweite Aktion')).toBeInTheDocument()
    expect(emitMock).toHaveBeenCalledWith('mss:karte-erledigt', { auftrag_id: 'job-1' })
  })

  it('schließt die Karte, wenn ein anderes Fenster sie beantwortet hat', async () => {
    render(<DesktopAktionKarte offenerAuftragId={null} />)
    await waitFor(() => expect(erledigtRuf).not.toBeNull())

    ereignisRuf!(anfrage('job-1', 'Erste Aktion'))
    expect(await screen.findByText('Erste Aktion')).toBeInTheDocument()

    erledigtRuf!({ payload: { auftrag_id: 'job-1' } })
    await waitFor(() => expect(screen.queryByText('Erste Aktion')).not.toBeInTheDocument())
    expect(bestaetigenMock).not.toHaveBeenCalled()
    expect(ergebnisMeldenMock).not.toHaveBeenCalled()
  })

  it('meldet keinen Fehler, wenn in Rust nichts mehr wartet', async () => {
    // Das andere Fenster war schneller: sein Ergebnis steht schon im Panel.
    // Ein `DESKTOP_TOOL_FAILED` von hier würde es im Wettlauf überschreiben.
    bestaetigenMock.mockRejectedValue('MSS_NICHTS_WARTET: Zu diesem Auftrag wartet keine Aktion.')
    render(<DesktopAktionKarte offenerAuftragId={null} />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())

    ereignisRuf!(anfrage('job-1', 'Erste Aktion'))
    fireEvent.click(await screen.findByRole('button', { name: /(mss\.aktion\.bestaetigen|Ja, ausführen|Yes, execute)/i }))

    await waitFor(() => expect(screen.queryByText('Erste Aktion')).not.toBeInTheDocument())
    expect(ergebnisMeldenMock).not.toHaveBeenCalled()
  })

  it('meldet einen echten Fehler weiterhin als DESKTOP_TOOL_FAILED', async () => {
    bestaetigenMock.mockRejectedValue('Computer-Use ist in der App ausgeschaltet.')
    render(<DesktopAktionKarte offenerAuftragId={null} />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())

    ereignisRuf!(anfrage('job-1', 'Erste Aktion'))
    fireEvent.click(await screen.findByRole('button', { name: /(mss\.aktion\.bestaetigen|Ja, ausführen|Yes, execute)/i }))

    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledWith(
      'job-1',
      false,
      { fehler: 'Computer-Use ist in der App ausgeschaltet.' },
      'DESKTOP_TOOL_FAILED',
    ))
  })
})
