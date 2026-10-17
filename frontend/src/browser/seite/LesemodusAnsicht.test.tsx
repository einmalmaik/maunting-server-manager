import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { toast } from '@/stores/toastStore'
import { saveNoteOffline } from '@/lib/offlineSync'
import { entwurfNehmen } from '@/lib/aiEntwurf'

import { leererTab, type Tab } from '../services/tab'
import { useSitzung } from '../services/sitzung'
import { useTabsStore } from '../services/tabsStore'
import { useLesemodusStore } from '../services/lesemodusStore'
import type { ExtrahierterArtikel } from '../services/lesemodusService'
import { LesemodusAnsicht } from './LesemodusAnsicht'

vi.mock('@/lib/offlineSync', () => ({
  saveNoteOffline: vi.fn().mockResolvedValue({ note: { id: 1 }, queued: false }),
}))

const mockArtikel: ExtrahierterArtikel = {
  titel: 'Test Artikel Titel',
  autor: 'Test Autor',
  datum: '2026-10-17',
  inhaltHtml:
    '<p>Erster Test-Absatz mit einem <a href="https://news.example.com/weiterer-beitrag">Weblink</a> und <a href="#abschnitt-2">Sprungmarke</a>.</p><h2 id="abschnitt-2">Abschnitt 2</h2><p>Zweiter Test-Absatz</p>',
  reintext: 'Erster Test-Absatz mit einem Weblink und Sprungmarke.\n\nAbschnitt 2\n\nZweiter Test-Absatz',
  wortanzahl: 12,
  lesedauerMinuten: 1,
  url: 'https://news.example.com/beitrag-1',
}

function rendernMitRouter(ui: ReactNode) {
  return render(
    <MemoryRouter initialEntries={['/browser']}>
      <Routes>
        <Route path="/browser" element={ui} />
        <Route path="/ai" element={<div data-testid="ai-chat">Singra AI Chat</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LesemodusAnsicht', () => {
  const tab: Tab = {
    ...leererTab('tab-lese'),
    url: 'https://news.example.com/beitrag-1',
    lesbar: true,
    lesemodus: true,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useSitzung.setState({ stand: 'an' })
    useLesemodusStore.setState({
      schriftgroesse: 'mittel',
      schriftart: 'sans',
      breite: 'standard',
      linksModus: 'dezent',
      artikelCache: {
        'tab-lese:https://news.example.com/beitrag-1': mockArtikel,
      },
    })
  })

  it('besitzt das Überdeckungs-Attribut data-msb-verdeckt="true" und rendert Artikel-Inhalte', () => {
    const { container } = rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const root = container.querySelector('[data-msb-verdeckt="true"]')
    expect(root).toBeInTheDocument()

    expect(screen.getByText('Test Artikel Titel')).toBeInTheDocument()
    expect(screen.getByText('Test Autor')).toBeInTheDocument()
    expect(screen.getByText('2026-10-17')).toBeInTheDocument()
    expect(screen.getByText(/Erster Test-Absatz/)).toBeInTheDocument()
    expect(screen.getByText(/Zweiter Test-Absatz/)).toBeInTheDocument()
  })

  it('beendet den Lesemodus beim Klick auf den Schließen-Knopf', () => {
    const lesemodusUmschalten = vi.fn()
    useTabsStore.setState({ lesemodusUmschalten } as any)

    rendernMitRouter(<LesemodusAnsicht tab={tab} />)
    const zurueckKnopf = screen.getByLabelText(/Lesemodus beenden/i)
    fireEvent.click(zurueckKnopf)

    expect(lesemodusUmschalten).toHaveBeenCalledWith('tab-lese')
  })

  it('übergibt Artikeltext an Singra (/ai) beim Klick auf Zusammenfassen', async () => {
    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const zusammenfassenKnopf = screen.getByLabelText(/Mit KI zusammenfassen/i)
    fireEvent.click(zusammenfassenKnopf)

    // Entwurf wurde übergeben
    const entwurf = entwurfNehmen()
    expect(entwurf).toContain('Test Artikel Titel')
    expect(entwurf).toContain('https://news.example.com/beitrag-1')
    expect(entwurf).toContain('Erster Test-Absatz')

    // Navigation nach /ai
    await waitFor(() => {
      expect(screen.getByTestId('ai-chat')).toBeInTheDocument()
    })
  })

  it('speichert in Notizen bei aktiver MSM-Kopplung (saveNoteOffline)', async () => {
    const toastSpy = vi.spyOn(toast, 'success')
    useSitzung.setState({ stand: 'an' }) // gekoppelt

    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const notizKnopf = screen.getByLabelText(/In Notizen speichern/i)
    expect(notizKnopf).not.toBeDisabled()

    await act(async () => {
      fireEvent.click(notizKnopf)
    })

    expect(saveNoteOffline).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Test Artikel Titel',
        note_type: 'personal',
        content: expect.stringContaining('Erster Test-Absatz'),
      }),
    )
    expect(toastSpy).toHaveBeenCalledWith(expect.stringContaining('Artikel als Notiz gespeichert'))
  })

  it('deaktiviert Notiz-Speichern bei fehlender Kopplung', async () => {
    const toastErrorSpy = vi.spyOn(toast, 'error')
    useSitzung.setState({ stand: 'aus' }) // ungekoppelt

    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const notizKnopf = screen.getByLabelText(/In Notizen speichern/i)
    expect(notizKnopf).toBeDisabled()

    await act(async () => {
      fireEvent.click(notizKnopf)
    })

    expect(saveNoteOffline).not.toHaveBeenCalled()
  })

  it('kopiert Artikeltext in die Zwischenablage', async () => {
    const writeTextSpy = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: { writeText: writeTextSpy },
    })
    const toastSpy = vi.spyOn(toast, 'success')

    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const kopierKnopf = screen.getByLabelText(/Text kopieren/i)
    await act(async () => {
      fireEvent.click(kopierKnopf)
    })

    expect(writeTextSpy).toHaveBeenCalledWith(
      expect.stringContaining('Test Artikel Titel\n\nErster Test-Absatz'),
    )
    expect(toastSpy).toHaveBeenCalledWith(expect.stringContaining('Artikeltext kopiert'))
  })

  it('öffnet das Typografie-Menü als Portal in document.body und schließt es bei Escape', () => {
    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const typografieKnopf = screen.getByLabelText(/Schriftgröße/i)
    fireEvent.click(typografieKnopf)

    // Dialog wurde per Portal in document.body gerendert
    const dialog = screen.getByRole('dialog', { name: /Schriftgröße/i })
    expect(dialog).toBeInTheDocument()
    expect(dialog.getAttribute('data-msb-verdeckt')).toBe('true')

    // Schriftgröße vergrößern
    const plusKnopf = screen.getByLabelText(/Schrift vergrößern/i)
    fireEvent.click(plusKnopf)
    expect(useLesemodusStore.getState().schriftgroesse).toBe('gross')

    // Schriftart ändern
    const serifKnopf = screen.getByRole('button', { name: /Serif/i })
    fireEvent.click(serifKnopf)
    expect(useLesemodusStore.getState().schriftart).toBe('serif')

    // Per Escape schließen
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: /Schriftgröße/i })).not.toBeInTheDocument()
  })

  it('richtet die Tooltips der rechten Aktions-Knöpfe nach innen aus (seite="ende" / right-0)', () => {
    const { container } = rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    // Alle Kurzinfo-Blasen der rechten Aktionsleiste müssen right-0 besitzen
    const headerRechts = container.querySelector('header .flex.items-center.gap-1\\.5, header .flex.items-center.gap-2')
    expect(headerRechts).toBeInTheDocument()

    const blasen = headerRechts!.querySelectorAll('[data-kurzinfo]')
    expect(blasen.length).toBe(4)
    blasen.forEach((blase) => {
      // right-0 sorgt dafür, dass der Tooltip bündig rechts am Knopf abschließt und nach links IN den Browser zeigt
      expect(blase).toHaveClass('right-0')
      expect(blase).not.toHaveClass('-translate-x-1/2')
    })
  })

  it('öffnet beim Klick auf einen Web-Link einen neuen Tab mit Eltern-Verknüpfung', () => {
    const neuerTabMock = vi.fn()
    useTabsStore.setState({ neuerTab: neuerTabMock } as any)

    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const link = screen.getByRole('link', { name: 'Weblink' })
    fireEvent.click(link)

    expect(neuerTabMock).toHaveBeenCalledWith('https://news.example.com/weiterer-beitrag', {
      elternId: 'tab-lese',
      hintergrund: false,
    })
  })

  it('öffnet Weblinks im Hintergrund bei Klick mit Strg-Taste oder Mittelklick (auxclick)', () => {
    const neuerTabMock = vi.fn()
    useTabsStore.setState({ neuerTab: neuerTabMock } as any)

    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const link = screen.getByRole('link', { name: 'Weblink' })

    // Strg-Klick
    fireEvent.click(link, { ctrlKey: true })
    expect(neuerTabMock).toHaveBeenCalledWith('https://news.example.com/weiterer-beitrag', {
      elternId: 'tab-lese',
      hintergrund: true,
    })

    // Mittelklick (auxclick button 1)
    fireEvent(link, new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 }))
    expect(neuerTabMock).toHaveBeenCalledWith('https://news.example.com/weiterer-beitrag', {
      elternId: 'tab-lese',
      hintergrund: true,
    })
  })

  it('öffnet keinen neuen Tab, wenn Links auf "Aus" gestellt sind', () => {
    const neuerTabMock = vi.fn()
    useTabsStore.setState({ neuerTab: neuerTabMock } as any)
    useLesemodusStore.setState({ linksModus: 'aus' })

    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const link = screen.getByRole('link', { name: 'Weblink' })
    fireEvent.click(link)
    fireEvent(link, new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 }))

    expect(neuerTabMock).not.toHaveBeenCalled()
  })

  it('scrollt bei Fragment-Links (#...) zum Zielelement oder zum Anfang', () => {
    const { container } = rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const ziel = container.querySelector('#abschnitt-2')
    expect(ziel).toBeInTheDocument()

    const scrollSpy = vi.fn()
    ziel!.scrollIntoView = scrollSpy

    const sprungLink = screen.getByRole('link', { name: 'Sprungmarke' })
    fireEvent.click(sprungLink)

    expect(scrollSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
  })

  it('erlaubt das Umschalten des Link-Modus (Dezent, Hervorgehoben, Aus) im Typografie-Menü', () => {
    rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    const typografieKnopf = screen.getByLabelText(/Schriftgröße/i)
    fireEvent.click(typografieKnopf)

    // Hervorgehoben auswählen
    const hervorgehobenKnopf = screen.getByRole('button', { name: /Hervorgehoben/i })
    fireEvent.click(hervorgehobenKnopf)
    expect(useLesemodusStore.getState().linksModus).toBe('hervorgehoben')

    // Aus auswählen
    const ausKnopf = screen.getByRole('button', { name: /Aus/i })
    fireEvent.click(ausKnopf)
    expect(useLesemodusStore.getState().linksModus).toBe('aus')

    // Dezent auswählen
    const dezentKnopf = screen.getByRole('button', { name: /Dezent/i })
    fireEvent.click(dezentKnopf)
    expect(useLesemodusStore.getState().linksModus).toBe('dezent')
  })

  it('wendet entsprechende Link-CSS-Klassen je nach Link-Modus an', () => {
    const { container } = rendernMitRouter(<LesemodusAnsicht tab={tab} />)

    // Dezent (Standard)
    let inhaltDiv = container.querySelector('.prose')
    expect(inhaltDiv?.className).toContain('decoration-outline-variant')
    expect(inhaltDiv?.className).toContain('[&_a:hover]:text-primary')

    // Hervorgehoben
    act(() => {
      useLesemodusStore.getState().setLinksModus('hervorgehoben')
    })
    inhaltDiv = container.querySelector('.prose')
    expect(inhaltDiv?.className).toContain('[&_a]:text-primary')

    // Aus
    act(() => {
      useLesemodusStore.getState().setLinksModus('aus')
    })
    inhaltDiv = container.querySelector('.prose')
    expect(inhaltDiv?.className).toContain('pointer-events-none')
  })
})
