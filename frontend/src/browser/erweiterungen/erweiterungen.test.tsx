import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const tauri = vi.hoisted(() => ({ invoke: vi.fn(), android: false }))
vi.mock('@tauri-apps/api/core', () => ({ invoke: tauri.invoke }))
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn(async () => () => {}) }))
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn() }))
vi.mock('../services/nativ', () => ({ istTauri: () => true }))
vi.mock('../services/plattform', () => ({ istAndroid: () => tauri.android }))
vi.mock('../services/tabsStore', () => ({ useTabsStore: Object.assign(() => vi.fn(), { getState: () => ({ neuerTab: vi.fn() }) }) }))

import { Erweiterungen } from '../einstellungen/Erweiterungen'
import { sichtbareKategorien } from '../einstellungen/kategorien'
import { erweiterungen, erweiterungenMoeglich, useErweiterungen, type Angaben, type ErweiterungenStand } from './erweiterungen'
import { Rueckfrage } from './Rueckfrage'

const ANGABEN: Angaben = {
  name: 'uBlock Origin Lite',
  version: '2026.1',
  beschreibung: '',
  rechte: ['declarativeNetRequest', 'storage', 'eigenesRecht'],
  seiten: ['<all_urls>'],
  optional: [],
  symbol: null,
  popup: 'popup.html',
  optionen: 'dashboard.html',
}
const ID = 'ddkjiahejlhfcafbddmgiahcphecmpfh'

function stand(teil: Partial<ErweiterungenStand> = {}): ErweiterungenStand {
  return {
    entwicklermodus: false,
    jugendschutz: false,
    eintraege: [{ id: ID, herkunft: 'store', an: false, laeuft: false, angeheftet: false, angaben: ANGABEN }],
    ...teil,
  }
}

beforeEach(() => {
  tauri.android = false
  tauri.invoke.mockReset()
  useErweiterungen.setState({ stand: null, popup: null, zuletztZu: null })
})

describe('Rückfrage vor der Installation', () => {
  it('nennt alle Seiten und jedes Recht, bekannte mit Namen, unbekannte wie im Manifest', () => {
    render(<Rueckfrage vorschau={{ vorgang: 'v1', id: ID, herkunft: 'store', angaben: ANGABEN }} fertig={() => {}} />)
    expect(screen.getByText('Kann alles auf allen Webseiten lesen und ändern')).toBeTruthy()
    expect(screen.getByText('Anfragen von Seiten blockieren oder ändern')).toBeTruthy()
    expect(screen.getByText('Daten speichern')).toBeTruthy()
    expect(screen.getByText('eigenesRecht')).toBeTruthy()
    expect(screen.getByText(/auch eingegebene Passwörter/)).toBeTruthy()
  })

  it('installiert erst auf Klick und verwirft beim Abbrechen', async () => {
    tauri.invoke.mockResolvedValue(stand())
    const fertig = vi.fn()
    const { unmount } = render(<Rueckfrage vorschau={{ vorgang: 'v1', id: ID, herkunft: 'store', angaben: ANGABEN }} fertig={fertig} />)
    expect(tauri.invoke).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Installieren' }))
    await waitFor(() => expect(fertig).toHaveBeenCalled())
    expect(tauri.invoke).toHaveBeenCalledWith('erweiterung_installieren', { vorgang: 'v1' })
    unmount()

    render(<Rueckfrage vorschau={{ vorgang: 'v2', id: ID, herkunft: 'store', angaben: ANGABEN }} fertig={fertig} />)
    fireEvent.click(screen.getByRole('button', { name: 'Abbrechen' }))
    expect(tauri.invoke).toHaveBeenCalledWith('erweiterung_verwerfen', { vorgang: 'v2' })
    expect(tauri.invoke).not.toHaveBeenCalledWith('erweiterung_installieren', { vorgang: 'v2' })
  })
})

describe('Jugendschutz', () => {
  it('sperrt Installieren und Einschalten, Ausschalten bleibt', async () => {
    tauri.invoke.mockResolvedValue(stand({ jugendschutz: true }))
    render(<Erweiterungen />)
    expect(await screen.findByText(/Solange der Jugend- und Suchtschutz an ist/)).toBeTruthy()
    expect((screen.getByRole('textbox', { name: 'Link oder Kennung der Erweiterung' }) as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByRole('switch', { name: 'uBlock Origin Lite' }).hasAttribute('disabled')).toBe(true)
  })
})

describe('Plattform', () => {
  it('gibt es unter Android nicht, weder Knopf noch Einstellungen', () => {
    expect(erweiterungenMoeglich()).toBe(true)
    expect(sichtbareKategorien(false).some((k) => k.id === 'erweiterungen')).toBe(true)
    tauri.android = true
    expect(erweiterungenMoeglich()).toBe(false)
    expect(sichtbareKategorien(true).some((k) => k.id === 'erweiterungen')).toBe(false)
  })
})

describe('Popup', () => {
  it('öffnet das Popup unter dem Knopf, ein zweiter Klick auf denselben Knopf schließt es nur', async () => {
    tauri.invoke.mockResolvedValue(undefined)
    const knopf = document.createElement('button')
    knopf.getBoundingClientRect = () => ({ right: 500, bottom: 40 }) as DOMRect
    await erweiterungen.popup(ID, knopf)
    expect(tauri.invoke).toHaveBeenCalledWith('erweiterung_popup', { id: ID, rechts: 500, oben: 44 })
    // Der Klick nimmt dem Popup den Fokus; es meldet sich zu, bevor der Klick ankommt.
    act(() => useErweiterungen.setState({ popup: null, zuletztZu: { id: ID, zeit: Date.now() } }))
    tauri.invoke.mockClear()
    await erweiterungen.popup(ID, knopf)
    expect(tauri.invoke).not.toHaveBeenCalled()
  })
})
