import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen, gesperrt } = vi.hoisted(() => ({
  gerufen: [] as { befehl: string; args: unknown }[],
  gesperrt: { eingerichtet: true, gespeichert: [] as unknown[] },
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: unknown) => {
    gerufen.push({ befehl, args })
    return Promise.resolve(null)
  },
}))
vi.mock('../services/tresorGesperrt', () => ({
  eingerichtet: async () => gesperrt.eingerichtet,
  speichern: async (...args: unknown[]) => void gesperrt.gespeichert.push(args),
}))

const { FormularLeiste } = await import('./FormularLeiste')
const { useFormulare } = await import('../services/formulare')
const { useSitzung } = await import('../services/sitzung')
const { useTabsStore } = await import('../services/tabsStore')
const { useVaultStore } = await import('@/desktop/vault/vaultStore')

const tab = useTabsStore.getState().aktivId!
const leiste = () =>
  render(
    <MemoryRouter>
      <FormularLeiste />
    </MemoryRouter>,
  )

describe('Leiste für Anmeldungen', () => {
  beforeEach(() => {
    gerufen.length = 0
    gesperrt.gespeichert.length = 0
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    useSitzung.setState({ stand: 'an' })
    useFormulare.setState({ feld: {}, abgeschickt: {}, schritt: {} })
    useVaultStore.setState({
      isUnlocked: true,
      items: [{ id: 'e1', service: 'example.com', url: 'https://example.com/', username: 'ada', password: 'Gipfel', createdAt: 0, updatedAt: 0, revision: 1 }],
    })
  })

  it('füllt erst auf Klick, und nur für die Herkunft, die der Tab gemeldet hat', async () => {
    act(() => useFormulare.setState({ feld: { [tab]: { url: 'https://example.com/login', neu: false } } }))
    leiste()
    expect(gerufen).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Als ada einfügen' }))
    await act(() => Promise.resolve())
    expect(gerufen).toContainEqual({
      befehl: 'tab_fuellen',
      args: { id: tab, fuer: 'https://example.com/login', werte: { benutzer: 'ada', passwort: 'Gipfel', neu: null } },
    })
  })

  it('bietet einer fremden Seite nichts an', () => {
    act(() => useFormulare.setState({ feld: { [tab]: { url: 'https://example.com.evil.test/login', neu: false } } }))
    leiste()
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })

  it('schlägt bei einer Registrierung ein Passwort vor und füllt beide Felder', async () => {
    act(() => useFormulare.setState({ feld: { [tab]: { url: 'https://neu.example/register', neu: true } } }))
    leiste()
    fireEvent.click(screen.getByRole('button', { name: 'Starkes Passwort vorschlagen' }))
    await act(() => Promise.resolve())
    const werte = (gerufen.find((g) => g.befehl === 'tab_fuellen')?.args as { werte: { neu: string } }).werte
    expect(werte.neu).toHaveLength(20)
  })

  it('speichert bei gesperrtem Tresor über den Posteingang, erst nach dem Klick', async () => {
    useVaultStore.setState({ isUnlocked: false, items: [] })
    act(() => useFormulare.setState({ abgeschickt: { [tab]: { url: 'https://example.com/login', benutzer: 'ada', passwort: 'Neu-1' } } }))
    leiste()
    const knopf = await screen.findByRole('button', { name: 'Speichern' })
    expect(gesperrt.gespeichert).toEqual([])
    fireEvent.click(knopf)
    await act(() => new Promise((r) => setTimeout(r, 0)))
    expect(gesperrt.gespeichert).toEqual([['https://example.com/login', 'ada', 'Neu-1']])
    expect(useFormulare.getState().abgeschickt[tab]).toBeUndefined()
  })

  it('zeigt im privaten Tab nichts', () => {
    useTabsStore.setState((s) => ({ tabs: s.tabs.map((t) => (t.id === tab ? { ...t, privat: true } : t)) }))
    act(() => useFormulare.setState({ feld: { [tab]: { url: 'https://example.com/login', neu: false } } }))
    leiste()
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    useTabsStore.setState((s) => ({ tabs: s.tabs.map((t) => (t.id === tab ? { ...t, privat: false } : t)) }))
  })
})
