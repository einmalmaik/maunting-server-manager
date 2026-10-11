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
const { Kopfleiste } = await import('./Kopfleiste')

const warten = () => act(async () => { await new Promise((r) => setTimeout(r, 0)) })
const UA = navigator.userAgent
const alsAndroid = (an: boolean) =>
  Object.defineProperty(navigator, 'userAgent', {
    value: an ? 'Mozilla/5.0 (Linux; Android 15; Pixel) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36' : UA,
    configurable: true,
  })

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

beforeEach(() => {
  alsAndroid(false)
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  gerufen.length = 0
  useEinstellungenStore.setState({ ausgeblendet: [] })
  useTabsStore.setState({
    tabs: [
      {
        id: 'tab-1',
        url: 'https://youtube.com/watch?v=123',
        titel: 'YouTube',
        privat: false,
        laedt: false,
        ton: false,
        stumm: false,
        ruhe: null,
        nativDa: true,
        letzterAufruf: Date.now(),
        erstellerId: null,
        favicon: null,
      },
      {
        id: 'tab-2',
        url: 'https://example.com/',
        titel: 'Example',
        privat: false,
        laedt: false,
        ton: false,
        stumm: false,
        ruhe: null,
        nativDa: true,
        letzterAufruf: Date.now(),
        erstellerId: null,
        favicon: null,
      },
    ],
    aktivId: 'tab-1',
  })
})

afterEach(() => {
  alsAndroid(false)
})

describe('Kopfleiste Tab-Auslagern (Tear-off)', () => {
  it('lagert einen Tab aus und schließt ihn im aktuellen Fenster, wenn er herausgezogen wird', async () => {
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })

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
        screenX: 200,
        screenY: 20,
        clientX: 200,
        clientY: 20,
        dataTransfer,
      }),
    )

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 1920,
        screenY: 300,
        clientX: 1920,
        clientY: 300,
      }),
    )

    await warten()

    const auslagernAufruf = gerufen.find((g) => g.befehl === 'tab_auslagern')
    expect(auslagernAufruf).toBeDefined()
    expect(auslagernAufruf?.args).toEqual({
      url: 'https://youtube.com/watch?v=123',
      x: 1920,
      y: 300,
      privat: false,
    })

    expect(useTabsStore.getState().tabs.map((t) => t.id)).toEqual(['tab-2'])
  })

  it('übergibt das privat-Flag beim Auslagern eines privaten Tabs', async () => {
    useTabsStore.setState((s) => ({
      tabs: [{ ...s.tabs[0], privat: true }, s.tabs[1]],
    }))
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })

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
        screenX: 200,
        screenY: 20,
        clientX: 200,
        clientY: 20,
        dataTransfer,
      }),
    )

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 500,
        screenY: 400,
        clientX: 500,
        clientY: 400,
      }),
    )

    await warten()

    const auslagernAufruf = gerufen.find((g) => g.befehl === 'tab_auslagern')
    expect(auslagernAufruf?.args).toMatchObject({
      privat: true,
    })
  })

  it('lagert einen Tab nicht aus, wenn es der einzige Tab im Fenster ist', async () => {
    useTabsStore.setState((s) => ({
      tabs: [s.tabs[0]],
      aktivId: 'tab-1',
    }))
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn(),
    }

    fireEvent(
      tab1,
      ziehEreignis('dragstart', {
        screenX: 100,
        screenY: 20,
        clientX: 100,
        clientY: 20,
        dataTransfer,
      }),
    )

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 800,
        screenY: 500,
        clientX: 800,
        clientY: 500,
      }),
    )

    await warten()

    expect(gerufen.find((g) => g.befehl === 'tab_auslagern')).toBeUndefined()
    expect(useTabsStore.getState().tabs.length).toBe(1)
  })

  it('reorganisiert Tabs beim Ablegen auf einem anderen Tab und lagert nicht aus', async () => {
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })
    const tab2 = screen.getByRole('tab', { name: /Example/ })

    const daten: Record<string, string> = {}
    const dataTransfer = {
      setData(format: string, val: string) {
        daten[format] = val
      },
      getData(format: string) {
        return daten[format] || ''
      },
    }

    fireEvent(
      tab1,
      ziehEreignis('dragstart', {
        screenX: 50,
        screenY: 20,
        clientX: 50,
        clientY: 20,
        dataTransfer,
      }),
    )

    fireEvent(tab2, ziehEreignis('dragover'))
    fireEvent(tab2, ziehEreignis('drop', { dataTransfer }))

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 200,
        screenY: 20,
        clientX: 200,
        clientY: 20,
      }),
    )

    await warten()

    expect(gerufen.find((g) => g.befehl === 'tab_auslagern')).toBeUndefined()
    expect(useTabsStore.getState().tabs.map((t) => t.id)).toEqual(['tab-2', 'tab-1'])
  })

  it('bricht das Auslagern ab, wenn Escape gedrückt wird', async () => {
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn(),
    }

    fireEvent(
      tab1,
      ziehEreignis('dragstart', {
        screenX: 50,
        screenY: 20,
        clientX: 50,
        clientY: 20,
        dataTransfer,
      }),
    )

    fireEvent.keyDown(window, { key: 'Escape' })

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 800,
        screenY: 500,
        clientX: 800,
        clientY: 500,
      }),
    )

    await warten()

    expect(gerufen.find((g) => g.befehl === 'tab_auslagern')).toBeUndefined()
    expect(useTabsStore.getState().tabs.length).toBe(2)
  })

  it('lagert unter Android keinen Tab aus', async () => {
    alsAndroid(true)
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn(),
    }

    fireEvent(
      tab1,
      ziehEreignis('dragstart', {
        screenX: 50,
        screenY: 20,
        clientX: 50,
        clientY: 20,
        dataTransfer,
      }),
    )

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 800,
        screenY: 500,
        clientX: 800,
        clientY: 500,
      }),
    )

    await warten()

    expect(gerufen.find((g) => g.befehl === 'tab_auslagern')).toBeUndefined()
    expect(useTabsStore.getState().tabs.length).toBe(2)
  })

  it('lagert nicht aus, wenn der Tab auf der Leiste fallengelassen wird', async () => {
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })
    const tablist = screen.getByRole('tablist')

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn(),
    }

    fireEvent(
      tab1,
      ziehEreignis('dragstart', {
        screenX: 50,
        screenY: 20,
        clientX: 50,
        clientY: 20,
        dataTransfer,
      }),
    )

    fireEvent(tablist, ziehEreignis('drop'))

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 100,
        screenY: 25,
        clientX: 100,
        clientY: 25,
      }),
    )

    await warten()

    expect(gerufen.find((g) => g.befehl === 'tab_auslagern')).toBeUndefined()
    expect(useTabsStore.getState().tabs.length).toBe(2)
  })

  it('lagert aus, wenn der Tab auf einen zweiten Bildschirm gezogen wird, auch wenn clientX/clientY 0 sind', async () => {
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn(),
    }

    fireEvent(
      tab1,
      ziehEreignis('dragstart', {
        screenX: 200,
        screenY: 20,
        clientX: 200,
        clientY: 20,
        dataTransfer,
      }),
    )

    // Auf zweitem Monitor abgelegt (außerhalb des Fensters meldet der Browser oft clientX/clientY = 0)
    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 2560,
        screenY: 200,
        clientX: 0,
        clientY: 0,
      }),
    )

    await warten()

    const aufruf = gerufen.find((g) => g.befehl === 'tab_auslagern')
    expect(aufruf).toBeDefined()
    expect(aufruf?.args).toEqual({
      url: 'https://youtube.com/watch?v=123',
      x: 2560,
      y: 200,
      privat: false,
    })
    expect(useTabsStore.getState().tabs.map((t) => t.id)).toEqual(['tab-2'])
  })

  it('lagert aus bei negativem screenY auf oberen Monitor', async () => {
    render(<Kopfleiste />)
    const tab1 = screen.getByRole('tab', { name: /YouTube/ })

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn(),
    }

    fireEvent(
      tab1,
      ziehEreignis('dragstart', {
        screenX: 200,
        screenY: 20,
        clientX: 200,
        clientY: 20,
        dataTransfer,
      }),
    )

    fireEvent(
      tab1,
      ziehEreignis('dragend', {
        screenX: 200,
        screenY: -350,
        clientX: 200,
        clientY: -350,
      }),
    )

    await warten()

    const aufruf = gerufen.find((g) => g.befehl === 'tab_auslagern')
    expect(aufruf).toBeDefined()
    expect(aufruf?.args).toEqual({
      url: 'https://youtube.com/watch?v=123',
      x: 200,
      y: -350,
      privat: false,
    })
  })
})
