import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen } = vi.hoisted(() => ({ gerufen: [] as { befehl: string; args: Record<string, unknown> }[] }))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    gerufen.push({ befehl, args })
    return Promise.resolve(null)
  },
}))

const { useTabsStore } = await import('../services/tabsStore')
const { useEinstellungenStore } = await import('../services/einstellungenStore')
const { VertikaleTableiste, baueTabBaum } = await import('./VertikaleTableiste')

const warten = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })

function ziehEreignis(
  typ: 'dragstart' | 'drag' | 'dragend' | 'drop' | 'dragover',
  init: { screenX?: number; screenY?: number; clientX?: number; clientY?: number; dataTransfer?: unknown } = {},
) {
  const ev = new Event(typ, { bubbles: true, cancelable: true })
  if (init.screenX !== undefined) Object.defineProperty(ev, 'screenX', { value: init.screenX })
  if (init.screenY !== undefined) Object.defineProperty(ev, 'screenY', { value: init.screenY })
  if (init.clientX !== undefined) Object.defineProperty(ev, 'clientX', { value: init.clientX })
  if (init.clientY !== undefined) Object.defineProperty(ev, 'clientY', { value: init.clientY })
  if (init.dataTransfer !== undefined) Object.defineProperty(ev, 'dataTransfer', { value: init.dataTransfer })
  return ev
}

describe('baueTabBaum', () => {
  it('strukturiert flache Tabs hierarchisch mit Ebenen und Sichtbarkeit', () => {
    const tabs = [
      {
        id: 'root-1',
        url: 'https://github.com/maunting/msm',
        titel: 'MSM Repo',
        favicon: null,
        laedt: false,
        zurueck: false,
        vor: false,
        privat: false,
        werbung: 0,
        tracker: 0,
        abgestuerzt: false,
        fehler: null,
        gesperrt: null,
        status: '',
        treffer: null,
        nativDa: true,
        ton: false,
        stumm: false,
        ruhe: null,
        vorher: null,
        elternId: null,
        baumEingeklappt: false,
      },
      {
        id: 'child-1',
        url: 'https://github.com/maunting/msm/blob/main/README.md',
        titel: 'README.md',
        favicon: null,
        laedt: false,
        zurueck: false,
        vor: false,
        privat: false,
        werbung: 0,
        tracker: 0,
        abgestuerzt: false,
        fehler: null,
        gesperrt: null,
        status: '',
        treffer: null,
        nativDa: true,
        ton: false,
        stumm: false,
        ruhe: null,
        vorher: null,
        elternId: 'root-1',
        baumEingeklappt: false,
      },
      {
        id: 'grandchild-1',
        url: 'https://github.com/maunting/msm/raw/main/screenshot.png',
        titel: 'screenshot.png',
        favicon: null,
        laedt: false,
        zurueck: false,
        vor: false,
        privat: false,
        werbung: 0,
        tracker: 0,
        abgestuerzt: false,
        fehler: null,
        gesperrt: null,
        status: '',
        treffer: null,
        nativDa: true,
        ton: false,
        stumm: false,
        ruhe: null,
        vorher: null,
        elternId: 'child-1',
        baumEingeklappt: false,
      },
      {
        id: 'root-2',
        url: 'https://wikipedia.org/',
        titel: 'Wikipedia',
        favicon: null,
        laedt: false,
        zurueck: false,
        vor: false,
        privat: false,
        werbung: 0,
        tracker: 0,
        abgestuerzt: false,
        fehler: null,
        gesperrt: null,
        status: '',
        treffer: null,
        nativDa: true,
        ton: false,
        stumm: false,
        ruhe: null,
        vorher: null,
        elternId: null,
        baumEingeklappt: false,
      },
    ]

    const baum = baueTabBaum(tabs)
    expect(baum.map((b) => ({ id: b.tab.id, ebene: b.ebene, hatKinder: b.hatKinder, sichtbar: b.sichtbar }))).toEqual([
      { id: 'root-1', ebene: 0, hatKinder: true, sichtbar: true },
      { id: 'child-1', ebene: 1, hatKinder: true, sichtbar: true },
      { id: 'grandchild-1', ebene: 2, hatKinder: false, sichtbar: true },
      { id: 'root-2', ebene: 0, hatKinder: false, sichtbar: true },
    ])
  })

  it('blendet Kind-Tabs aus, wenn der Eltern-Tab eingeklappt ist', () => {
    const tabs = [
      {
        id: 'root-1',
        url: 'https://github.com/',
        titel: 'GitHub',
        favicon: null,
        laedt: false,
        zurueck: false,
        vor: false,
        privat: false,
        werbung: 0,
        tracker: 0,
        abgestuerzt: false,
        fehler: null,
        gesperrt: null,
        status: '',
        treffer: null,
        nativDa: true,
        ton: false,
        stumm: false,
        ruhe: null,
        vorher: null,
        elternId: null,
        baumEingeklappt: true,
      },
      {
        id: 'child-1',
        url: 'https://github.com/repo',
        titel: 'Repo',
        favicon: null,
        laedt: false,
        zurueck: false,
        vor: false,
        privat: false,
        werbung: 0,
        tracker: 0,
        abgestuerzt: false,
        fehler: null,
        gesperrt: null,
        status: '',
        treffer: null,
        nativDa: true,
        ton: false,
        stumm: false,
        ruhe: null,
        vorher: null,
        elternId: 'root-1',
        baumEingeklappt: false,
      },
    ]

    const baum = baueTabBaum(tabs)
    expect(baum[0].sichtbar).toBe(true)
    expect(baum[1].sichtbar).toBe(false)
  })
})

describe('VertikaleTableiste Komponente', () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    gerufen.length = 0
    useEinstellungenStore.setState({
      ausgeblendet: [],
      vertikaleTabsEingeklappt: false,
      tabAusrichtung: 'vertikal',
    })
    useTabsStore.setState({
      tabs: [
        {
          id: 'tab-gh',
          url: 'https://github.com/maunting',
          titel: 'GitHub Profil',
          favicon: 'https://github.com/favicon.ico',
          privat: false,
          laedt: false,
          ton: false,
          stumm: false,
          ruhe: null,
          nativDa: true,
          elternId: null,
          baumEingeklappt: false,
          zurueck: false,
          vor: false,
          werbung: 0,
          tracker: 0,
          abgestuerzt: false,
          fehler: null,
          gesperrt: null,
          status: '',
          treffer: null,
          vorher: null,
        },
        {
          id: 'tab-datei',
          url: 'https://github.com/maunting/msm/blob/main/server.py',
          titel: 'server.py',
          favicon: 'https://github.com/favicon.ico',
          privat: false,
          laedt: false,
          ton: false,
          stumm: false,
          ruhe: null,
          nativDa: true,
          elternId: 'tab-gh',
          baumEingeklappt: false,
          zurueck: false,
          vor: false,
          werbung: 0,
          tracker: 0,
          abgestuerzt: false,
          fehler: null,
          gesperrt: null,
          status: '',
          treffer: null,
          vorher: null,
        },
      ],
      aktivId: 'tab-gh',
    })
  })

  it('zeigt im ausgeklappten Zustand Seitennamen, Favicon und Schließen-Button', () => {
    render(<VertikaleTableiste />)

    expect(screen.getByText('GitHub Profil')).toBeInTheDocument()
    expect(screen.getByText('server.py')).toBeInTheDocument()

    // Schließen-Buttons vorhanden
    const schliessenBtns = screen.getAllByRole('button', { name: /schließen/i })
    expect(schliessenBtns.length).toBe(2)

    // Klick auf Schließen schließt den Tab
    fireEvent.click(schliessenBtns[1])
    expect(useTabsStore.getState().tabs.map((t) => t.id)).toEqual(['tab-gh'])
  })

  it('klappt Unter-Tabs per Klick auf das Chevron ein und aus', () => {
    render(<VertikaleTableiste />)

    const chevronBtn = screen.getByRole('button', { name: /Untergeordnete Tabs einklappen/i })
    expect(chevronBtn).toBeInTheDocument()

    // Einklappen
    fireEvent.click(chevronBtn)
    expect(useTabsStore.getState().tabs.find((t) => t.id === 'tab-gh')?.baumEingeklappt).toBe(true)

    // Kind-Tab ist nicht mehr gerendert
    expect(screen.queryByText('server.py')).toBeNull()
    // Zähler +1 sichtbar
    expect(screen.getByText('+1')).toBeInTheDocument()

    // Wieder ausklappen
    const ausklappenBtn = screen.getByRole('button', { name: /Untergeordnete Tabs ausklappen/i })
    fireEvent.click(ausklappenBtn)
    expect(useTabsStore.getState().tabs.find((t) => t.id === 'tab-gh')?.baumEingeklappt).toBe(false)
    expect(screen.getByText('server.py')).toBeInTheDocument()
  })

  it('zeigt im eingeklappten Zustand nur die Favicons und keinen Schließen-Button', () => {
    useEinstellungenStore.setState({ vertikaleTabsEingeklappt: true })
    render(<VertikaleTableiste />)

    // Text nicht im DOM der Knöpfe als Fließtext sichtbar
    expect(screen.queryByText('GitHub Profil')).toBeNull()

    // Keine Schließen-Buttons im eingeklappten Zustand
    expect(screen.queryByRole('button', { name: /schließen/i })).toBeNull()

    // Aber Tab-Buttons mit Tooltip (Kurzinfo) und Favicons sind klickbar
    const tabButtons = screen.getAllByRole('treeitem')
    expect(tabButtons.length).toBe(2)

    // Klick auf den zweiten Tab aktiviert ihn
    fireEvent.click(tabButtons[1])
    expect(useTabsStore.getState().aktivId).toBe('tab-datei')
  })

  it('bietet einen Knopf zum Ein- und Ausklappen der gesamten Leiste', () => {
    const { rerender } = render(<VertikaleTableiste />)

    const einklappenBtn = screen.getByRole('button', { name: /Tableiste einklappen/i })
    fireEvent.click(einklappenBtn)
    expect(useEinstellungenStore.getState().vertikaleTabsEingeklappt).toBe(true)

    rerender(<VertikaleTableiste />)
    const ausklappenBtn = screen.getByRole('button', { name: /Tableiste ausklappen/i })
    fireEvent.click(ausklappenBtn)
    expect(useEinstellungenStore.getState().vertikaleTabsEingeklappt).toBe(false)
  })

  it('lagert einen Tab beim Herausziehen aus (Tear-off)', async () => {
    render(<VertikaleTableiste />)
    const tab1 = screen.getAllByRole('treeitem')[0]

    const dataTransfer = {
      data: {} as Record<string, string>,
      setData(format: string, data: string) {
        this.data[format] = data
      },
      getData(format: string) {
        return this.data[format] || ''
      },
    }

    fireEvent(
      tab1,
      ziehEreignis('dragstart', {
        screenX: 100,
        screenY: 100,
        clientX: 50,
        clientY: 50,
        dataTransfer,
      }),
    )

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 800,
        screenY: 600,
        clientX: 800,
        clientY: 600,
      }),
    )

    await warten()

    const auslagernAufruf = gerufen.find((g) => g.befehl === 'tab_auslagern')
    expect(auslagernAufruf).toBeDefined()
    expect(auslagernAufruf?.args).toEqual({
      url: 'https://github.com/maunting',
      x: 800,
      y: 600,
      privat: false,
    })
  })
})
