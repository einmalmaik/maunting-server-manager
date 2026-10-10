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
const { useUeberdeckungBeobachten, useStandbild } = await import('./ueberdeckung')
const { useTabsStore } = await import('./tabsStore')

const warten = () => act(() => new Promise((r) => setTimeout(r, 40)))

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

    await vi.waitFor(() => expect(offen.find((o) => o.befehl === 'tab_standbild')).toBeDefined())
    const standbild = offen.find((o) => o.befehl === 'tab_standbild')!
    standbild.fertig(new ArrayBuffer(4))

    await vi.waitFor(() =>
      expect(
        offen.find((o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === true),
      ).toBeDefined(),
    )
    // Erst das Bild, dann verschwindet der Tab.
    expect(useStandbild.getState().bild).toEqual({ tab: 'tab-a', url: 'blob:standbild' })
    const verdecken = offen.find(
      (o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === true,
    )!
    expect(verdecken.args).toEqual({ verdeckt: true })
    verdecken.fertig()

    fenster.remove()
    await vi.waitFor(() =>
      expect(
        offen.find((o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === false),
      ).toBeDefined(),
    )
    const zeigen = offen.find(
      (o) => o.befehl === 'tabs_verdecken' && (o.args as { verdeckt?: boolean })?.verdeckt === false,
    )!
    expect(zeigen.args).toEqual({ verdeckt: false })
    zeigen.fertig()
    await warten()
    expect(useStandbild.getState().bild).toBeNull()
  })
})
