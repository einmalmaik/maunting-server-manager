import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const nativ = vi.hoisted(() => ({ tabAufnahme: vi.fn(), aufnahmeSpeichern: vi.fn(), downloadZeigen: vi.fn() }))
vi.mock('../services/nativ', () => ({ nativ }))
vi.mock('../services/ueberdeckung', () => ({ seiteFrei: async () => true }))
const geraet = vi.hoisted(() => ({ android: false, tab: undefined as unknown }))
vi.mock('../services/plattform', () => ({ istAndroid: () => geraet.android }))
vi.mock('../services/tabsStore', () => ({ useAktiverTab: () => geraet.tab }))

import { OHNE_EMULATION, useAnsicht } from '../entwickler/werkzeuge'
import { leererTab } from '../services/tab'
import { useAufnahme } from './ablauf'
import { AufnahmeKnopf, aufnahmeMoeglich } from './AufnahmeKnopf'
import { Aufnahmen } from './Aufnahmen'
import { base64AusBlob, dateiname, pixel, pngAusBase64 } from './bild'

/** Ein PNG aus einem Pixel. */
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
const TAB = { id: 'tab-a', url: 'https://www.example.org/seite' }

beforeEach(() => {
  useAufnahme.setState(useAufnahme.getInitialState(), true)
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => `blob:${Math.random()}`), revokeObjectURL: vi.fn() }))
  nativ.tabAufnahme.mockReset().mockResolvedValue({ png: PNG, abgeschnitten: false })
  nativ.aufnahmeSpeichern.mockReset()
})
afterEach(() => {
  act(() => useAufnahme.getState().schliessen())
  vi.restoreAllMocks()
})

describe('Bilder', () => {
  it('rechnet einen Ausschnitt in ganze Pixel innerhalb des Bildes um', () => {
    expect(pixel({ x: 0.25, y: 0.5, breite: 0.5, hoehe: 0.25 }, 800, 400)).toEqual({ x: 200, y: 200, breite: 400, hoehe: 100 })
    expect(pixel({ x: 0.9, y: 0.9, breite: 0.5, hoehe: 0.5 }, 100, 100)).toEqual({ x: 90, y: 90, breite: 10, hoehe: 10 })
    expect(pixel({ x: 1, y: 1, breite: 0, hoehe: 0 }, 100, 50)).toEqual({ x: 99, y: 49, breite: 1, hoehe: 1 })
  })

  it('benennt die Datei nach Seite und Ortszeit, ohne Doppelpunkt', () => {
    expect(dateiname(TAB.url, new Date(2026, 9, 10, 4, 5, 6))).toBe('Screenshot example.org 2026-10-10 04-05-06.png')
    expect(dateiname('about:blank', new Date(2026, 0, 2, 3, 4, 5))).toBe('Screenshot 2026-01-02 03-04-05.png')
  })

  it('gibt dieselben Bytes zurück, die es bekommen hat', async () => {
    expect(await base64AusBlob(pngAusBase64(PNG))).toBe(PNG)
  })

  it('nimmt nur Seiten auf, die zu sehen sind', () => {
    expect(aufnahmeMoeglich({ ...leererTab('a'), url: TAB.url })).toBe(true)
    expect(aufnahmeMoeglich(leererTab('a'))).toBe(false)
    expect(aufnahmeMoeglich({ ...leererTab('a'), url: 'msb://einstellungen' })).toBe(false)
    expect(aufnahmeMoeglich({ ...leererTab('a'), url: TAB.url, fehler: 'gesperrt' })).toBe(false)
  })
})

describe('Ablauf', () => {
  it('nimmt die ganze Seite nur auf Wunsch und sagt, wenn sie abgeschnitten ist', async () => {
    nativ.tabAufnahme.mockResolvedValue({ png: PNG, abgeschnitten: true })
    render(<Aufnahmen />)
    await act(() => useAufnahme.getState().aufnehmen(TAB, 'ganz'))
    expect(nativ.tabAufnahme).toHaveBeenCalledWith('tab-a', true)
    expect(screen.getByRole('dialog', { name: 'Screenshot' })).toBeTruthy()
    expect(screen.getByText(/länger, als sich auf einmal aufnehmen lässt/)).toBeTruthy()
  })

  it('speichert unter dem Namen der Seite und bietet an, die Datei zu zeigen', async () => {
    nativ.aufnahmeSpeichern.mockResolvedValue('C:\\Downloads\\Screenshot example.org.png')
    render(<Aufnahmen />)
    await act(() => useAufnahme.getState().aufnehmen(TAB, 'sichtbar'))
    expect(nativ.tabAufnahme).toHaveBeenCalledWith('tab-a', false)
    fireEvent.click(screen.getByRole('button', { name: 'Als PNG speichern' }))
    await waitFor(() => expect(useAufnahme.getState().stand).toBeNull())
    const [id, name, url, png] = nativ.aufnahmeSpeichern.mock.calls[0]
    expect([id, url, png]).toEqual(['tab-a', TAB.url, PNG])
    expect(name).toMatch(/^Screenshot example\.org \d{4}-\d\d-\d\d \d\d-\d\d-\d\d\.png$/)
  })

  it('bietet Kopieren nur an, wo die WebView Bilder kopieren kann', async () => {
    render(<Aufnahmen />)
    await act(() => useAufnahme.getState().aufnehmen(TAB, 'sichtbar'))
    expect(screen.queryByRole('button', { name: 'In Zwischenablage kopieren' })).toBeNull()
  })

  it('meldet, wenn die Aufnahme scheitert, und zeigt keinen Dialog', async () => {
    nativ.tabAufnahme.mockRejectedValue('Nur der Tab, der vorne liegt')
    render(<Aufnahmen />)
    await act(() => useAufnahme.getState().aufnehmen(TAB, 'sichtbar'))
    await waitFor(() => expect(useAufnahme.getState().stand).toBeNull())
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('Bereich auswählen', () => {
  const ziehen = (von: [number, number], nach: [number, number]) => {
    const flaeche = screen.getByRole('dialog', { name: 'Bereich für den Screenshot wählen' })
    flaeche.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}) })
    flaeche.setPointerCapture = () => {}
    fireEvent.pointerDown(flaeche, { button: 0, clientX: von[0], clientY: von[1], pointerId: 1 })
    fireEvent.pointerMove(flaeche, { clientX: nach[0], clientY: nach[1], pointerId: 1 })
    fireEvent.pointerUp(flaeche, { clientX: nach[0], clientY: nach[1], pointerId: 1 })
  }

  it('nimmt den gezogenen Bereich in Anteilen des Bildes, auch rückwärts gezogen', async () => {
    render(<Aufnahmen />)
    await act(() => useAufnahme.getState().aufnehmen(TAB, 'auswahl'))
    const auswaehlen = vi.spyOn(useAufnahme.getState(), 'auswaehlen').mockResolvedValue()
    ziehen([300, 150], [100, 50])
    expect(auswaehlen).toHaveBeenCalledWith({ x: 0.25, y: 0.25, breite: 0.5, hoehe: 0.5 })
  })

  it('übergeht einen bloßen Klick und bricht mit Escape ab', async () => {
    render(<Aufnahmen />)
    await act(() => useAufnahme.getState().aufnehmen(TAB, 'auswahl'))
    const auswaehlen = vi.spyOn(useAufnahme.getState(), 'auswaehlen').mockResolvedValue()
    ziehen([100, 100], [102, 101])
    expect(auswaehlen).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(useAufnahme.getState().stand).toBeNull()
  })
})

describe('Knopf', () => {
  it('bietet die ganze Seite nur unter Windows an und nimmt nach dem Klick auf', async () => {
    geraet.tab = { ...leererTab('tab-a'), url: TAB.url }
    for (const android of [false, true]) {
      geraet.android = android
      const { unmount } = render(<AufnahmeKnopf />)
      fireEvent.click(screen.getByRole('button', { name: 'Screenshot' }))
      expect(!!screen.queryByRole('button', { name: 'Ganze Seite' })).toBe(!android)
      unmount()
    }
    geraet.android = false
    render(<AufnahmeKnopf />)
    fireEvent.click(screen.getByRole('button', { name: 'Screenshot' }))
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Sichtbarer Bereich' })))
    expect(nativ.tabAufnahme).toHaveBeenCalledWith('tab-a', false)
  })

  it('lässt eine Gerätenachbildung der Entwicklerwerkzeuge stehen', () => {
    geraet.tab = { ...leererTab('tab-a'), url: TAB.url }
    geraet.android = false
    useAnsicht.setState({ emulation: { 'tab-a': { ...OHNE_EMULATION, geraet: 'iphone15' } } })
    render(<AufnahmeKnopf />)
    fireEvent.click(screen.getByRole('button', { name: 'Screenshot' }))
    expect(screen.queryByRole('button', { name: 'Ganze Seite' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Sichtbarer Bereich' })).toBeTruthy()
    useAnsicht.setState({ emulation: {} })
  })

  it('ist auf der Startseite aus', () => {
    geraet.tab = leererTab('tab-a')
    render(<AufnahmeKnopf />)
    expect(screen.getByRole('button', { name: 'Screenshot' }).hasAttribute('disabled')).toBe(true)
  })
})
