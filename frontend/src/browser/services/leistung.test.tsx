import { fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen } = vi.hoisted(() => ({ gerufen: [] as { befehl: string; args: Record<string, unknown> }[] }))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    gerufen.push({ befehl, args })
    return Promise.resolve(null)
  },
}))

const { useTabsStore } = await import('./tabsStore')
const { useEinstellungenStore } = await import('./einstellungenStore')
const { ausnahmeHost, useLeistung } = await import('./leistung')
const { Kopfleiste } = await import('../kopf/Kopfleiste')
const { Leistung } = await import('../einstellungen/Leistung')

const warten = () => new Promise((r) => setTimeout(r, 0))
const tab = (id: string) => useTabsStore.getState().tabs.find((t) => t.id === id)!

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  const vorlage = useTabsStore.getState().tabs[0]
  useTabsStore.setState({
    tabs: [
      { ...vorlage, id: 'tab-vorne', url: 'https://a.example/', titel: 'Vorne', nativDa: true },
      { ...vorlage, id: 'tab-hinten', url: 'https://b.example/', titel: 'Hinten', nativDa: true },
    ],
    aktivId: 'tab-vorne',
  })
  useEinstellungenStore.setState({ schlafenNach: 30, speicherSparen: false, schlafAusnahmen: [] })
  gerufen.length = 0
})

describe('Schlafende und verworfene Tabs', () => {
  it('lädt einen verworfenen Tab beim Zeigen neu, einen schlafenden nicht', async () => {
    const { ereignis, aktivieren } = useTabsStore.getState()
    ereignis({ art: 'schlaf', id: 'tab-hinten', schlaeft: true })
    expect(tab('tab-hinten').ruhe).toBe('schlaf')
    aktivieren('tab-hinten')
    await warten()
    expect(gerufen.map((g) => g.befehl)).not.toContain('tab_laden')

    aktivieren('tab-vorne')
    ereignis({ art: 'verworfen', id: 'tab-hinten' })
    expect(tab('tab-hinten')).toMatchObject({ ruhe: 'verworfen', nativDa: false })
    // Ein spätes „wach“ aus der alten Webview ändert daran nichts.
    ereignis({ art: 'schlaf', id: 'tab-hinten', schlaeft: false })
    expect(tab('tab-hinten').ruhe).toBe('verworfen')
    gerufen.length = 0
    aktivieren('tab-hinten')
    await warten()
    expect(gerufen.find((g) => g.befehl === 'tab_laden')?.args).toMatchObject({ id: 'tab-hinten', url: 'https://b.example/' })
    expect(tab('tab-hinten').ruhe).toBeNull()
  })

  it('zeigt Mond und Lautsprecher am Tab, ein Klick schaltet stumm', async () => {
    const { ereignis } = useTabsStore.getState()
    ereignis({ art: 'schlaf', id: 'tab-hinten', schlaeft: true })
    ereignis({ art: 'ton', id: 'tab-vorne', spielt: true, stumm: false })
    render(<Kopfleiste />)
    expect(screen.getByRole('tab', { name: /Hinten, schläft/ })).toBeInTheDocument()
    const vorne = screen.getByRole('tab', { name: /Vorne/ })
    fireEvent.click(within(vorne).getByRole('button', { name: '„Vorne“ stummschalten' }))
    await warten()
    expect(gerufen.find((g) => g.befehl === 'tab_stumm')?.args).toEqual({ id: 'tab-vorne', stumm: true })
    expect(useTabsStore.getState().aktivId).toBe('tab-vorne')
  })
})

describe('Regeln an Rust', () => {
  it('schickt „nie“ als null und jede Änderung neu', async () => {
    renderHook(() => useLeistung())
    await warten()
    expect(gerufen.at(-1)).toEqual({ befehl: 'tabs_leistung', args: { schlafenMinuten: 30, verwerfen: false, ausnahmen: [] } })
    useEinstellungenStore.setState({ schlafenNach: 0, speicherSparen: true })
    await warten()
    expect(gerufen.at(-1)?.args).toEqual({ schlafenMinuten: null, verwerfen: true, ausnahmen: [] })
  })

  it('nimmt als Ausnahme nur eine Domain', () => {
    expect(ausnahmeHost('YouTube.com')).toBe('youtube.com')
    expect(ausnahmeHost('https://www.youtube.com/watch?v=1')).toBe('youtube.com')
    expect(ausnahmeHost('*.twitch.tv')).toBe('twitch.tv')
    expect(ausnahmeHost('localhost')).toBeNull()
    expect(ausnahmeHost('zwei wörter')).toBeNull()
    expect(ausnahmeHost('javascript:alert(1)')).toBeNull()
  })

  it('legt Ausnahmen unter Leistung an und lehnt Unsinn ab', () => {
    render(<Leistung />)
    const feld = screen.getByRole('textbox', { name: 'Seite' })
    fireEvent.change(feld, { target: { value: 'nix' } })
    fireEvent.click(screen.getByRole('button', { name: 'Hinzufügen' }))
    expect(screen.getByText('Das ist keine Domain.')).toBeInTheDocument()
    fireEvent.change(feld, { target: { value: 'https://music.example.org/x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Hinzufügen' }))
    expect(useEinstellungenStore.getState().schlafAusnahmen).toEqual(['music.example.org'])
  })
})
