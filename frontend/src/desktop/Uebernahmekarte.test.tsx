/**
 * Die Übernahmekarte — wen sie beantwortet.
 *
 * Diese Karte ist der einzige Weg zur Freigabe von Maus und Tastatur, und sie
 * meldet das Ergebnis des Auftrags selbst: solange er auf einen Menschen
 * wartet, hat er keins. Genau deshalb zählt, **welchem** Auftrag die Antwort
 * gutgeschrieben wird. Die Kennung dafür steht in der Nutzlast des Ereignisses
 * (auftrag.rs → `steuern`); der Zustand der Oberfläche ist nur noch der
 * Rückfall für eine App, deren Rust-Hälfte sie noch nicht mitschickt.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

// i18n ist im Test nicht geladen: `t()` gibt den Schlüssel zurück. Die Knöpfe
// werden deshalb über ihre Schlüssel gegriffen und nicht über deutschen Text.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const freigebenMock = vi.fn()
const restMock = vi.fn()
const ergebnisMeldenMock = vi.fn()
let ereignisRuf: ((e: { payload: unknown }) => void) | null = null

vi.mock('@tauri-apps/api/event', () => ({
  listen: (_name: string, rueckruf: (e: { payload: unknown }) => void) => {
    ereignisRuf = rueckruf
    return Promise.resolve(() => {})
  },
}))

vi.mock('./tauri', () => ({
  uebernahmeFreigeben: (...args: unknown[]) => freigebenMock(...args),
  uebernahmeWiderrufen: () => Promise.resolve(),
  uebernahmeRest: () => restMock(),
}))

vi.mock('./desktopJobs', () => ({
  ergebnisMelden: (...args: unknown[]) => ergebnisMeldenMock(...args),
}))

import { Uebernahmekarte } from './Uebernahmekarte'

const ANLIEGEN = 'Das Fenster lässt sich nur per Klick schließen'

describe('Uebernahmekarte', () => {
  beforeEach(() => {
    ereignisRuf = null
    freigebenMock.mockReset().mockResolvedValue(undefined)
    // Keine laufende Freigabe — sonst zeigt die Karte den Reststreifen.
    restMock.mockReset().mockResolvedValue(0)
    ergebnisMeldenMock.mockReset().mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('zeigt ohne Ereignis nichts', () => {
    const { container } = render(<Uebernahmekarte offenerAuftragId={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('beantwortet den Auftrag aus dem Ereignis, nicht den aus dem Zustand', async () => {
    // Die Zuordnung hing daran, dass die Auftragsschleife ihre Kennung noch
    // vor dem Ereignis in den Zustand bekommt. Kommt das Ereignis zuerst —
    // oder liegt dort noch die Kennung des vorigen Auftrags —, ginge die
    // Freigabe an den falschen Auftrag: Maus und Tastatur sind frei, und
    // erledigt gemeldet wird etwas anderes.
    render(<Uebernahmekarte offenerAuftragId="job-alt" />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())
    ereignisRuf!({ payload: { anliegen: ANLIEGEN, minuten: 3, auftrag_id: 'job-neu' } })

    fireEvent.click(await screen.findByRole('button', { name: /uebernahme\.freigeben/i }))

    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(1))
    const [jobId, ok, inhalt] = ergebnisMeldenMock.mock.calls[0]
    expect(jobId).toBe('job-neu')
    expect(ok).toBe(true)
    expect(inhalt).toMatchObject({ freigegeben: true, minuten: 3 })
    // Und die Frist ist die des Ereignisses, nicht irgendeine.
    expect(freigebenMock).toHaveBeenCalledWith(3)
  })

  it('nimmt die Kennung der Schleife, wenn das Ereignis ohne Kennung kommt', async () => {
    // Eine App, deren Rust-Hälfte die Kennung noch nicht mitschickt, darf
    // nicht stumm werden — sonst verfällt jede Bitte um die Übernahme.
    render(<Uebernahmekarte offenerAuftragId="job-alt" />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())
    ereignisRuf!({ payload: { anliegen: ANLIEGEN, minuten: 2 } })

    fireEvent.click(await screen.findByRole('button', { name: /uebernahme\.freigeben/i }))

    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(1))
    expect(ergebnisMeldenMock.mock.calls[0][0]).toBe('job-alt')
  })

  it('gibt bei Ablehnung nichts frei und meldet trotzdem', async () => {
    render(<Uebernahmekarte offenerAuftragId="job-alt" />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())
    ereignisRuf!({ payload: { anliegen: ANLIEGEN, minuten: 5, auftrag_id: 'job-neu' } })

    fireEvent.click(await screen.findByRole('button', { name: /uebernahme\.ablehnen/i }))

    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(1))
    const [jobId, ok, inhalt] = ergebnisMeldenMock.mock.calls[0]
    expect(jobId).toBe('job-neu')
    expect(ok).toBe(true)
    expect(inhalt).toMatchObject({ freigegeben: false, minuten: 0 })
    expect(freigebenMock).not.toHaveBeenCalled()
  })
})
