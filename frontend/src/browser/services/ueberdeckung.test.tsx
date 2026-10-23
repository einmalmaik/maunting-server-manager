import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const offen: { befehl: string; args: unknown; fertig: (wert?: unknown) => void }[] = []

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: unknown) =>
    new Promise((fertig) => {
      offen.push({ befehl, args, fertig })
    }),
}))

const { nativ } = await import('./nativ')
const { useUeberdeckungBeobachten, useStandbild, bytesZuBlob } = await import('./ueberdeckung')
const { useTabsStore } = await import('./tabsStore')

const warten = (ms = 150) => act(() => new Promise((r) => setTimeout(r, ms)))

function Wurzel() {
  useUeberdeckungBeobachten()
  return null
}

describe('Sichtbarkeit der Tabs', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    offen.length = 0
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    URL.createObjectURL = vi.fn(() => 'blob:standbild')
    URL.revokeObjectURL = vi.fn()
    useTabsStore.setState({ aktivId: 'tab-a', split: null })
    useStandbild.setState({ bilder: {} })
  })
  afterEach(() => {
    document.body.innerHTML = ''
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
  })

  it('schickt verdecken und zeigen nacheinander, nie gleichzeitig', async () => {
    const erst = nativ.tabsVerdecken(true)
    const dann = nativ.tabsVerdecken(false)
    await Promise.resolve()
    await Promise.resolve()
    // Solange Rust das erste nicht bestätigt hat, ist das zweite nicht unterwegs.
    const verdecken = () => offen.filter((o) => o.befehl === 'tabs_verdecken')
    expect(verdecken().map((o) => o.args)).toEqual([{ verdeckt: true }])
    verdecken()[0].fertig()
    await erst
    await vi.waitFor(() => expect(verdecken()).toHaveLength(2))
    expect(verdecken()[1].args).toEqual({ verdeckt: false })
    verdecken()[1].fertig()
    await dann
  })

  it('zeigt die Seite als Standbild, solange ein Fenster darüber liegt', async () => {
    useTabsStore.setState({ aktivId: 'tab-a' })
    render(<Wurzel />)
    const fenster = document.createElement('div')
    fenster.setAttribute('data-ankerfenster', '')
    document.body.appendChild(fenster)

    await vi.waitFor(() => expect(offen.find((o) => o.befehl === 'tab_standbild')).toBeDefined(), { timeout: 5000 })
    const standbild = offen.find((o) => o.befehl === 'tab_standbild')!
    expect(standbild.args).toEqual({ id: 'tab-a' })
    standbild.fertig(new ArrayBuffer(4))

    await vi.waitFor(
      () =>
        expect(
          offen.find((o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === true),
        ).toBeDefined(),
      { timeout: 5000 },
    )
    // Erst das Bild, dann verschwindet der Tab.
    expect(useStandbild.getState().bilder).toEqual({ 'tab-a': 'blob:standbild' })
    const verdecken = offen.find(
      (o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === true,
    )!
    expect(verdecken.args).toEqual({ verdeckt: true })
    verdecken.fertig()

    fenster.remove()
    await vi.waitFor(
      () =>
        expect(
          offen.find((o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === false),
        ).toBeDefined(),
      { timeout: 5000 },
    )
    const zeigen = offen.find(
      (o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === false,
    )!
    expect(zeigen.args).toEqual({ verdeckt: false })
    zeigen.fertig()
    await warten(300)
    expect(useStandbild.getState().bilder).toEqual({})
  })

  it('nimmt in der geteilten Ansicht beide Seiten als Standbild auf', async () => {
    useTabsStore.setState({
      aktivId: 'tab-a',
      split: { primaerId: 'tab-a', sekundaerId: 'tab-b', richtung: 'vertikal', anteil: 0.5, aktiveHaelfte: 'primaer' },
    })
    render(<Wurzel />)
    const fenster = document.createElement('div')
    fenster.setAttribute('data-ankerfenster', '')
    document.body.appendChild(fenster)

    await vi.waitFor(() => expect(offen.filter((o) => o.befehl === 'tab_standbild')).toHaveLength(1), { timeout: 5000 })
    const erste = offen.filter((o) => o.befehl === 'tab_standbild')[0]
    expect(erste.args).toEqual({ id: 'tab-a' })
    erste.fertig(new ArrayBuffer(4))

    // Die zweite Seite hat ihre eigene Webview; ohne ihr Bild bliebe sie leer.
    await vi.waitFor(() => expect(offen.filter((o) => o.befehl === 'tab_standbild')).toHaveLength(2), { timeout: 5000 })
    const zweite = offen.filter((o) => o.befehl === 'tab_standbild')[1]
    expect(zweite.args).toEqual({ id: 'tab-b' })
    zweite.fertig(new ArrayBuffer(4))

    await vi.waitFor(
      () =>
        expect(Object.keys(useStandbild.getState().bilder).sort()).toEqual(['tab-a', 'tab-b']),
      { timeout: 5000 },
    )

    // Aufräumen: erst das Verdecken abschließen, dann das Zeigen, sonst
    // bliebe die Befehlskette für den nächsten Test stehen.
    offen
      .find((o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === true)!
      .fertig()
    fenster.remove()
    await vi.waitFor(
      () =>
        expect(
          offen.find((o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === false),
        ).toBeDefined(),
      { timeout: 5000 },
    )
    offen
      .find((o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === false)!
      .fertig()
    await warten(300)
    expect(useStandbild.getState().bilder).toEqual({})
  })

  it('wandelt rohe Zahlen-Arrays aus der IPC korrekt in Blob um', async () => {
    const blobAusArray = bytesZuBlob([255, 216, 255, 224])
    expect(blobAusArray).not.toBeNull()
    expect(blobAusArray?.type).toBe('image/jpeg')
    const bufferAusArray = await blobAusArray!.arrayBuffer()
    expect(Array.from(new Uint8Array(bufferAusArray))).toEqual([255, 216, 255, 224])

    const blobAusBuffer = bytesZuBlob(new Uint8Array([255, 216, 255, 224]).buffer)
    expect(blobAusBuffer).not.toBeNull()
    expect(blobAusBuffer?.type).toBe('image/jpeg')

    expect(bytesZuBlob(null)).toBeNull()
    expect(bytesZuBlob([])).toBeNull()
  })

  it('ignoriert Dropdowns und Menüs innerhalb von Seitenpanels (.msb-bereich oder aside)', async () => {
    useTabsStore.setState({ aktivId: 'tab-a' })
    render(<Wurzel />)

    const panel = document.createElement('aside')
    const listbox = document.createElement('div')
    listbox.setAttribute('role', 'listbox')
    panel.appendChild(listbox)
    document.body.appendChild(panel)

    const portaledMenu = document.createElement('div')
    portaledMenu.setAttribute('role', 'listbox')
    portaledMenu.setAttribute('data-msm-in-panel', '')
    document.body.appendChild(portaledMenu)

    await warten(250)
    // Kein tabs_verdecken aufgerufen
    expect(offen.filter((o) => o.befehl === 'tabs_verdecken')).toHaveLength(0)

    panel.remove()
    portaledMenu.remove()
  })
})
