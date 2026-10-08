import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen, gesperrt } = vi.hoisted(() => ({
  gerufen: [] as { befehl: string; args: Record<string, unknown> }[],
  gesperrt: { eingerichtet: true, gespeichert: [] as unknown[][] },
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    gerufen.push({ befehl, args })
    return Promise.resolve(null)
  },
}))
vi.mock('../services/tresorGesperrt', () => ({
  eingerichtet: async () => gesperrt.eingerichtet,
  speichern: async (...args: unknown[]) => void gesperrt.gespeichert.push(args),
}))

const { FormularLeiste } = await import('./FormularLeiste')
const { erzeugtSpeichern } = await import('./PasswortErzeugen')
const { useFormulare } = await import('../services/formulare')
const { useSitzung } = await import('../services/sitzung')
const { useTabsStore } = await import('../services/tabsStore')
const { useVaultStore } = await import('@/desktop/vault/vaultStore')
const i18n = (await import('@/i18n')).default

const tab = useTabsStore.getState().aktivId!
const URL_ = 'https://neu.example/register'
const leiste = () =>
  render(
    <MemoryRouter>
      <FormularLeiste />
    </MemoryRouter>,
  )
const warten = () => act(() => new Promise((fertig) => setTimeout(fertig, 0)))
const fuellungen = () => gerufen.filter((g) => g.befehl === 'tab_fuellen').map((g) => (g.args.werte as { neu: string | null }).neu)
const fokus = () => act(() => useFormulare.getState().ereignis({ art: 'formular', id: tab, url: URL_, meldung: { t: 'feld', passwort: true, neu: true, sicher: true } }))
const absenden = (passwort: string, benutzer = 'ada@neu.example') =>
  act(() => useFormulare.getState().ereignis({ art: 'formular', id: tab, url: URL_, meldung: { t: 'absenden', benutzer, passwort, neu: true } }))

describe('Passwort erzeugen und nach der Registrierung speichern', () => {
  const saveItem = vi.fn(async () => undefined)
  const unlock = vi.fn(async () => true)

  beforeEach(() => {
    gerufen.length = 0
    gesperrt.eingerichtet = true
    gesperrt.gespeichert.length = 0
    saveItem.mockClear()
    unlock.mockClear()
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    useSitzung.setState({ stand: 'an' })
    useFormulare.setState({ feld: {}, abgeschickt: {}, schritt: {}, erzeugt: {} })
    useVaultStore.setState({ isUnlocked: true, items: [], saveItem, unlock })
  })

  it('setzt ohne Klick ein starkes Passwort ein und speichert es nach dem Absenden ohne Rückfrage', async () => {
    fokus()
    leiste()
    await warten()
    const [passwort] = fuellungen()
    expect(passwort).toHaveLength(20)
    expect(screen.getByRole('region')).toHaveTextContent('Starkes Passwort für neu.example eingesetzt')

    absenden(passwort!)
    await warten()
    expect(saveItem).toHaveBeenCalledWith({ service: 'neu.example', category: 'login', url: URL_, username: 'ada@neu.example', password: passwort })
    expect(screen.queryByRole('button', { name: 'Speichern' })).not.toBeInTheDocument()
  })

  it('legt es bei gesperrtem Tresor in den Posteingang, ohne zu entsperren', async () => {
    useVaultStore.setState({ isUnlocked: false })
    fokus()
    leiste()
    await warten()
    const [passwort] = fuellungen()
    expect(screen.getByRole('region')).toHaveTextContent('ohne ihn zu entsperren')

    absenden(passwort!)
    await warten()
    expect(gesperrt.gespeichert).toEqual([[URL_, 'ada@neu.example', passwort]])
    expect(unlock).not.toHaveBeenCalled()
    expect(saveItem).not.toHaveBeenCalled()
  })

  it('erzeugt nichts von selbst, wenn es danach nicht gespeichert werden könnte', async () => {
    useVaultStore.setState({ isUnlocked: false })
    gesperrt.eingerichtet = false
    fokus()
    leiste()
    await warten()
    expect(fuellungen()).toEqual([])
    expect(screen.getByRole('button', { name: 'Starkes Passwort vorschlagen' })).toBeInTheDocument()
  })

  // Bis 09.10.2026 legte jedes Absenden mit dem erzeugten Passwort einen
  // Eintrag an; die Seite kennt es und kann beliebig oft abschicken.
  it('speichert ein erzeugtes Passwort nur einmal ohne Rückfrage, danach fragt es', async () => {
    fokus()
    leiste()
    await warten()
    const [passwort] = fuellungen()
    absenden(passwort!)
    await warten()
    absenden(passwort!, 'zweiter@neu.example')
    await warten()
    absenden(passwort!, 'dritter@neu.example')
    await warten()
    expect(saveItem).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Speichern' })).toBeInTheDocument()
  })

  it('fragt wie bisher, wenn ein anderes Passwort abgeschickt wurde', async () => {
    fokus()
    leiste()
    await warten()
    absenden('selbst-getippt')
    await warten()
    expect(saveItem).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Speichern' })).toBeInTheDocument()
  })

  it('überschreibt ein vorhandenes Passwort nicht ohne Rückfrage', async () => {
    useVaultStore.setState({
      items: [{ id: 'e1', service: 'neu.example', url: URL_, username: 'ada@neu.example', password: 'alt', createdAt: 0, updatedAt: 0, revision: 1 }],
    })
    fokus()
    leiste()
    await warten()
    absenden(fuellungen()[0]!)
    await warten()
    expect(saveItem).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Aktualisieren' })).toBeInTheDocument()
  })

  it('„Eigenes Passwort“ leert die Felder und erzeugt auf der Seite kein neues', async () => {
    fokus()
    const { unmount } = leiste()
    await warten()
    fireEvent.click(screen.getByRole('button', { name: 'Eigenes Passwort' }))
    await warten()
    expect(fuellungen().at(-1)).toBe('')
    unmount()
    leiste()
    await warten()
    expect(fuellungen()).toHaveLength(2)
  })

  it('setzt auf derselben Seite nichts doppelt ein, nach einem Neuladen dasselbe Passwort', async () => {
    fokus()
    const { unmount } = leiste()
    await warten()
    unmount()
    leiste()
    await warten()
    expect(fuellungen()).toHaveLength(1)
    act(() => useFormulare.getState().laedt(tab))
    fokus()
    await warten()
    expect(fuellungen()).toHaveLength(2)
    expect(fuellungen()[1]).toBe(fuellungen()[0])
  })

  it('speichert eine Anmeldung nur einmal, auch wenn zweimal angestoßen', async () => {
    useFormulare.setState({ abgeschickt: { [tab]: { url: URL_, benutzer: 'ada', passwort: 'Erzeugt-1', erzeugt: true } } })
    await Promise.all([erzeugtSpeichern(tab, i18n.t), erzeugtSpeichern(tab, i18n.t)])
    expect(saveItem).toHaveBeenCalledTimes(1)
  })
})
