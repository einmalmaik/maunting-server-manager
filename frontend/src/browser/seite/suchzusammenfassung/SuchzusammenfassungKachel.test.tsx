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

    // Quellenbutton [1] existiert und scrollt beim Klick zum Treffer. Der Name
    // nennt die Nummer, damit die Kachel mehrere Quellknöpfe zeigen kann.
    const quelleBtn = screen.getByRole('button', { name: 'Quelle 1' })
    fireEvent.click(quelleBtn)
    expect(zielTreffer.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })

    document.body.removeChild(zielTreffer)
  })

  it('erlaubt das Einklappen und Ausklappen der Kachel', async () => {
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

    render(<SuchzusammenfassungKachel begriff="Testsuche" treffer={TREFFER} />)

    expect(await screen.findByText('Fertige Zusammenfassung')).toBeInTheDocument()

    // Einklappen-Button anklicken
    const einklappenBtn = screen.getByRole('button', { name: /Einklappen|Collapse/i })
    fireEvent.click(einklappenBtn)

    // Inhalt ist eingeklappt
    expect(screen.queryByText('Fertige Zusammenfassung')).not.toBeInTheDocument()

    // Ausklappen-Button anklicken
    const ausklappenBtn = screen.getByRole('button', { name: /Ausklappen|Expand/i })
    fireEvent.click(ausklappenBtn)

    // Inhalt ist wieder da
    expect(screen.getByText('Fertige Zusammenfassung')).toBeInTheDocument()
  })

  it('zeigt die selbständige Nachrecherche mit den Suchanfragen', async () => {
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
                'data: {"type": "start", "model": "gpt-4o"}\n\n' +
                  'data: {"type": "recherche", "anfrage": "Grafikkarte einbauen Anleitung"}\n\n' +
                  'data: {"type": "recherche", "anfrage": "Grafikkarte Stromanschluss"}\n\n' +
                  'data: {"type": "content", "text": "So geht der Einbau [1]."}\n\n' +
                  'data: {"type": "done"}\n\n',
              )
              return Promise.resolve({ done: false, value: chunk })
            },
          }
        },
      },
    })

    render(<SuchzusammenfassungKachel begriff="Testsuche" treffer={TREFFER} />)

    expect(await screen.findByText(/Sucht weiter|Researching further/)).toBeInTheDocument()
    expect(screen.getByText(/Grafikkarte einbauen Anleitung/)).toBeInTheDocument()
    expect(screen.getByText(/Grafikkarte Stromanschluss/)).toBeInTheDocument()
    expect(await screen.findByText(/So geht der Einbau/)).toBeInTheDocument()
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

    // DNA Dropdown-Button ist gerendert
    const dropdownBtn = screen.getByRole('button', { name: /Denkstufe/i })
    expect(dropdownBtn).toBeInTheDocument()
    expect(screen.getByText(/Schnell|Fast/i)).toBeInTheDocument()
  })

  it('klappt die Quellen als Logo-Leiste und nur auf Wunsch als Karten', async () => {
    let readCount = 0
    apiStream.mockResolvedValueOnce({
      ok: true,
      body: {
        getReader: () => ({
          read: () => {
            readCount += 1
            if (readCount === 1) {
              return Promise.resolve({
                done: false,
                value: new TextEncoder().encode(
                  'data: {"type": "start", "model": "m"}\n\ndata: {"type": "content", "text": "Text"}\n\ndata: {"type": "done"}\n\n',
                ),
              })
            }
            return Promise.resolve({ done: true, value: undefined })
          },
        }),
      },
    })

    render(<SuchzusammenfassungKachel begriff="Testsuche" treffer={TREFFER} />)
    expect(await screen.findByText('Text')).toBeInTheDocument()

    // Eingeklappt: genau ein Symbol je Quelle, kein Titeltext der Karte.
    expect(screen.getAllByRole('button', { name: 'example.com' })).toHaveLength(2)
    expect(screen.queryByText('Zweiter Treffer')).not.toBeInTheDocument()

    // Ein Klick klappt die Vorschaukarten mit Titel auf.
    fireEvent.click(screen.getByRole('button', { name: /Top-Quellen 2 ein- oder ausblenden/ }))
    expect(await screen.findByText('Zweiter Treffer')).toBeInTheDocument()
  })

  it('sendet bei der ersten Folgefrage Ausgangsfrage und Antwort als alternierenden Verlauf', async () => {
    // Initiale Zusammenfassung
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
                'data: {"type": "start", "model": "m"}\n\ndata: {"type": "content", "text": "Initiale Antwort"}\n\ndata: {"type": "done"}\n\n',
              )
              return Promise.resolve({ done: false, value: chunk })
            },
          }
        },
      },
    })
    // Folgefrage-Antwort
    apiStream.mockResolvedValueOnce({
      ok: true,
      body: {
        getReader: () => ({
          read: () => Promise.resolve({ done: true, value: undefined }),
        }),
      },
    })

    render(<SuchzusammenfassungKachel begriff="Ausgangsfrage" treffer={TREFFER} />)
    expect(await screen.findByText('Initiale Antwort')).toBeInTheDocument()

    const eingabe = screen.getByRole('textbox')
    fireEvent.change(eingabe, { target: { value: 'Und nachts?' } })
    fireEvent.click(screen.getByRole('button', { name: /Senden|Send/i }))

    await waitFor(() => {
      expect(apiStream).toHaveBeenCalledTimes(2)
    })
    const folgeBody = JSON.parse(apiStream.mock.calls[1][1].body as string)
    // user (Ausgangsfrage) -> assistant (hauptText) -> user (Folgefrage)
    expect(folgeBody.verlauf.map((e: { role: string }) => e.role)).toEqual(['user', 'assistant', 'user'])
    expect(folgeBody.verlauf[0].content).toBe('Ausgangsfrage')
    expect(folgeBody.verlauf[1].content).toBe('Initiale Antwort')
    expect(folgeBody.verlauf[2].content).toBe('Und nachts?')
  })

  it('zeigt bei Stream-Fehler einen Wiederholen-Knopf statt ewigem Spinner', async () => {
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
                'data: {"type": "start", "model": "m"}\n\ndata: {"type": "content", "text": "Initiale Antwort"}\n\ndata: {"type": "done"}\n\n',
              )
              return Promise.resolve({ done: false, value: chunk })
            },
          }
        },
      },
    })
    apiStream.mockResolvedValueOnce({
      ok: true,
      body: {
        getReader: () => {
          let gesendet = false
          return {
            read: () => {
              if (gesendet) return Promise.resolve({ done: true, value: undefined })
              gesendet = true
              const chunk = new TextEncoder().encode('data: {"type": "error", "message": "Upstream boom"}\n\n')
              return Promise.resolve({ done: false, value: chunk })
            },
          }
        },
      },
    })

    render(<SuchzusammenfassungKachel begriff="Ausgangsfrage" treffer={TREFFER} />)
    expect(await screen.findByText('Initiale Antwort')).toBeInTheDocument()

    const eingabe = screen.getByRole('textbox')
    fireEvent.change(eingabe, { target: { value: 'Folgefrage?' } })
    fireEvent.click(screen.getByRole('button', { name: /Senden|Send/i }))

    // Fehlermeldung mit Retry statt "Antwort wird generiert …" im Dauer-Spinner
    expect(await screen.findByRole('button', { name: /Erneut versuchen|Try again/i })).toBeInTheDocument()
    expect(screen.queryByText('Upstream boom')).toBeInTheDocument()
  })
})

