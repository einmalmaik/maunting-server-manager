import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiStream } = vi.hoisted(() => ({
  apiStream: vi.fn(),
}))

vi.mock('@/api/client', async (original) => ({
  ...(await original<object>()),
  apiStream,
}))

const { SuchzusammenfassungKachel } = await import('./SuchzusammenfassungKachel')

const TREFFER = [
  { titel: 'Erster Treffer', url: 'https://example.com/1', inhalt: 'Beschreibung eins' },
  { titel: 'Zweiter Treffer', url: 'https://example.com/2', inhalt: 'Beschreibung zwei' },
]

describe('SuchzusammenfassungKachel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('bleibt unsichtbar wenn das Feature deaktiviert ist', async () => {
    // SSE stream liefert sofort "disabled"
    apiStream.mockResolvedValueOnce({
      ok: true,
      body: {
        getReader: () => {
          let gesendet = false
          return {
            read: () => {
              if (gesendet) return Promise.resolve({ done: true, value: undefined })
              gesendet = true
              const chunk = new TextEncoder().encode('data: {"type": "disabled", "reason": "not_configured"}\n\n')
              return Promise.resolve({ done: false, value: chunk })
            },
          }
        },
      },
    })

    const { container } = render(<SuchzusammenfassungKachel begriff="Testsuche" treffer={TREFFER} />)
    await waitFor(() => {
      expect(container.firstChild).toBeNull()
    })
  })

  it('zeigt Ladezustand und streamt Markdown-Inhalt mit klickbaren Quellenverweisen', async () => {
    let readCount = 0
    apiStream.mockResolvedValueOnce({
      ok: true,
      body: {
        getReader: () => ({
          read: () => {
            readCount++
            if (readCount === 1) {
              const chunk = new TextEncoder().encode('data: {"type": "start", "model": "gpt-6-luna"}\n\n')
              return Promise.resolve({ done: false, value: chunk })
            }
            if (readCount === 2) {
              const chunk = new TextEncoder().encode('data: {"type": "content", "text": "Hier ist die Antwort [1]."}\n\n')
              return Promise.resolve({ done: false, value: chunk })
            }
            if (readCount === 3) {
              const chunk = new TextEncoder().encode('data: {"type": "done"}\n\n')
              return Promise.resolve({ done: false, value: chunk })
            }
            return Promise.resolve({ done: true, value: undefined })
          },
        }),
      },
    })

    // DOM-Ziel für die Verlinkung vorbereiten
    const zielTreffer = document.createElement('div')
    zielTreffer.id = 'treffer-1'
    zielTreffer.scrollIntoView = vi.fn()
    document.body.appendChild(zielTreffer)

    render(<SuchzusammenfassungKachel begriff="Testsuche" treffer={TREFFER} />)

    // Modellname und Text erscheinen
    expect(await screen.findByText('gpt-6-luna')).toBeInTheDocument()
    expect(await screen.findByText(/Hier ist die Antwort/)).toBeInTheDocument()

    // Quellenbutton [1] existiert und scrollt beim Klick zum Treffer
    const quelleBtn = screen.getByRole('button', { name: /1/ })
    fireEvent.click(quelleBtn)
    expect(zielTreffer.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })

    document.body.removeChild(zielTreffer)
  })

  it('erlaubt das Einklappen und Schließen der Kachel', async () => {
    apiStream.mockResolvedValueOnce({
      ok: true,
      body: {
        getReader: () => {
          let gesendet = false
          return {
            read: () => {
              if (gesendet) return Promise.resolve({ done: true, value: undefined })
              gesendet = true
              const chunk = new TextEncoder().encode(
                'data: {"type": "start", "model": "gpt-4o"}\n\ndata: {"type": "content", "text": "Fertige Zusammenfassung"}\n\ndata: {"type": "done"}\n\n',
              )
              return Promise.resolve({ done: false, value: chunk })
            },
          }
        },
      },
    })

    const { container } = render(<SuchzusammenfassungKachel begriff="Testsuche" treffer={TREFFER} />)

    expect(await screen.findByText('Fertige Zusammenfassung')).toBeInTheDocument()

    // Schließen-Button anklicken
    const schliessenBtn = screen.getByRole('button', { name: /Ausblenden|Dismiss/i })
    fireEvent.click(schliessenBtn)

    // Kachel ist ausgeblendet
    expect(container.firstChild).toBeNull()
  })

  it('zeigt Denkprozess und erlaubt Denkstufe bei Nachfragen wenn erlaubt', async () => {
    apiStream.mockResolvedValueOnce({
      ok: true,
      body: {
        getReader: () => {
          let gesendet = false
          return {
            read: () => {
              if (gesendet) return Promise.resolve({ done: true, value: undefined })
              gesendet = true
              const chunk = new TextEncoder().encode(
                'data: {"type": "start", "model": "o3-mini", "denkstufen": ["low", "medium", "high"], "max_denkstufe": "high"}\n\n' +
                'data: {"type": "thought", "text": "Ich analysiere die Quellen..."}\n\n' +
                'data: {"type": "content", "text": "Ergebnis mit Denkstufe"}\n\n' +
                'data: {"type": "done"}\n\n',
              )
              return Promise.resolve({ done: false, value: chunk })
            },
          }
        },
      },
    })

    render(<SuchzusammenfassungKachel begriff="Testsuche" treffer={TREFFER} />)

    expect(await screen.findByText('Ergebnis mit Denkstufe')).toBeInTheDocument()
    expect(screen.getByText('o3-mini')).toBeInTheDocument()
    // Denkprozess ist in details vorhanden
    expect(screen.getByText('Ich analysiere die Quellen...')).toBeInTheDocument()

    // Denkstufe-Select ist gerendert
    const select = screen.getByRole('combobox')
    expect(select).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Schnell|Fast/i })).toBeInTheDocument()
  })
})

