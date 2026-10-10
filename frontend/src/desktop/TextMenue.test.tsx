import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TextMenue } from './TextMenue'

const ablage = { writeText: vi.fn(async () => {}), readText: vi.fn(async () => 'aus der Ablage') }

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', { value: ablage, configurable: true })
  ablage.writeText.mockClear()
  document.execCommand = vi.fn(() => true)
})

afterEach(() => {
  document.body.innerHTML = ''
  window.getSelection()?.removeAllRanges()
})

function rechtsklick(ziel: Element) {
  const ereignis = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 10, clientY: 10 })
  act(() => {
    ziel.dispatchEvent(ereignis)
  })
  return ereignis
}

function markieren(knoten: Node) {
  const bereich = document.createRange()
  bereich.selectNodeContents(knoten)
  window.getSelection()?.removeAllRanges()
  window.getSelection()?.addRange(bereich)
}

describe('Rechtsklick in der App', () => {
  it('nimmt das Menü der WebView überall weg und zeigt ohne Auswahl nichts', () => {
    render(<TextMenue />)
    const absatz = document.body.appendChild(document.createElement('p'))
    absatz.textContent = 'Antwort der KI'
    expect(rechtsklick(absatz).defaultPrevented).toBe(true)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('kopiert markierten Text, etwa eine Antwort der KI', async () => {
    render(<TextMenue />)
    const absatz = document.body.appendChild(document.createElement('p'))
    absatz.textContent = 'Antwort der KI'
    markieren(absatz)
    rechtsklick(absatz)
    expect(screen.queryByRole('menuitem', { name: 'Einfügen' })).toBeNull()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Kopieren' }))
    await waitFor(() => expect(ablage.writeText).toHaveBeenCalledWith('Antwort der KI'))
  })

  it('fügt in ein Eingabefeld ein, über die Bearbeitung des Browsers', async () => {
    render(<TextMenue />)
    const feld = document.body.appendChild(document.createElement('textarea'))
    rechtsklick(feld)
    expect(screen.queryByRole('menuitem', { name: 'Kopieren' })).toBeNull()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Einfügen' }))
    await waitFor(() => expect(document.execCommand).toHaveBeenCalledWith('insertText', false, 'aus der Ablage'))
    expect(document.activeElement).toBe(feld)
  })

  it('kopiert nichts aus einem Passwortfeld und fügt nicht in gesperrte Felder ein', () => {
    render(<TextMenue />)
    const passwort = document.body.appendChild(document.createElement('input'))
    passwort.type = 'password'
    passwort.value = 'geheim'
    passwort.setSelectionRange(0, 6)
    rechtsklick(passwort)
    expect(screen.queryByRole('menuitem', { name: 'Kopieren' })).toBeNull()
    expect(screen.getByRole('menuitem', { name: 'Einfügen' })).toBeTruthy()

    const gesperrt = document.body.appendChild(document.createElement('input'))
    gesperrt.readOnly = true
    rechtsklick(gesperrt)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('lässt einen Rechtsklick, den ein Bereich selbst behandelt', () => {
    render(<TextMenue />)
    const nachricht = document.body.appendChild(document.createElement('div'))
    nachricht.textContent = 'Nachricht'
    nachricht.addEventListener('contextmenu', (e) => e.preventDefault())
    markieren(nachricht)
    rechtsklick(nachricht)
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
