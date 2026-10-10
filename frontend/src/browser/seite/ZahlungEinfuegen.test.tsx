import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen, hello } = vi.hoisted(() => ({
  gerufen: [] as { befehl: string; args: Record<string, unknown> }[],
  hello: { da: true, bestaetigt: true },
}))

// Die Klicksperre neuer Leisten prüft `FormularLeiste.test.tsx`; hier wird sofort geklickt.
vi.mock('./klickSperre', () => ({ useKlickSperre: () => () => {} }))
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    gerufen.push({ befehl, args })
    if (befehl === 'biometrie_verfuegbar') return Promise.resolve(hello.da)
    if (befehl === 'biometrie_verifizieren') return Promise.resolve(hello.bestaetigt)
    return Promise.resolve(null)
  },
}))

const { FormularLeiste } = await import('./FormularLeiste')
const { useFormulare } = await import('../services/formulare')
const { useSitzung } = await import('../services/sitzung')
const { useTabsStore } = await import('../services/tabsStore')
const { useVaultStore } = await import('@/desktop/vault/vaultStore')

const tab = useTabsStore.getState().aktivId!
const basis = { username: '', password: '', createdAt: 0, updatedAt: 0, revision: 1 }
const karte = { art: 'karte' as const, nummer: '4111111111111111', inhaber: 'Ada', monat: 12, jahr: 2030, pruefnummer: '123' }
const leiste = () =>
  render(
    <MemoryRouter>
      <FormularLeiste />
    </MemoryRouter>,
  )
const feld = (url: string, zahlung?: 'karte' | 'konto', rahmen?: string) =>
  act(() => useFormulare.setState({ feld: { [tab]: { url, neu: false, zahlung, rahmen } } }))
const gefuellt = () => gerufen.filter((g) => g.befehl === 'tab_fuellen')
const warten = () => act(() => new Promise((fertig) => setTimeout(fertig, 0)))

describe('Zahlungsmittel einfügen', () => {
  beforeEach(() => {
    gerufen.length = 0
    hello.da = true
    hello.bestaetigt = true
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    useSitzung.setState({ stand: 'an' })
    useFormulare.setState({ feld: {}, abgeschickt: {}, schritt: {} })
    useVaultStore.setState({
      isUnlocked: true,
      items: [
        { ...basis, id: 'k1', service: 'Privatkarte', category: 'zahlung', zahlung: karte },
        { ...basis, id: 'l1', service: 'shop.example', url: 'https://shop.example/', username: 'ada', password: 'Gipfel' },
      ],
    })
  })

  it('füllt erst nach Windows Hello, mit Karte und Seite in der Rückfrage', async () => {
    feld('https://shop.example/kasse', 'karte')
    leiste()
    expect(screen.queryByRole('button', { name: /Als ada/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '•••• 1111 einfügen' }))
    await warten()
    const frage = gerufen.find((g) => g.befehl === 'biometrie_verifizieren')
    expect(frage?.args.nachricht).toBe('Privatkarte •••• 1111 auf shop.example einfügen')
    expect(gefuellt()).toEqual([
      {
        befehl: 'tab_fuellen',
        args: { id: tab, fuer: 'https://shop.example/kasse', werte: { benutzer: null, passwort: null, neu: null, karte: { nummer: karte.nummer, inhaber: 'Ada', monat: 12, jahr: 2030, pruefnummer: '123' } } },
      },
    ])
  })

  it('nennt bei einem Kartenfeld in einem Rahmen dessen Anbieter und füllt nur dorthin', async () => {
    feld('https://shop.example/kasse', 'karte', 'https://js.stripe.com')
    leiste()
    expect(screen.getByText('Karte für shop.example aus dem Tresor, in das Formular von js.stripe.com. Eingefügt wird erst nach Bestätigung.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '•••• 1111 einfügen' }))
    await warten()
    const frage = gerufen.find((g) => g.befehl === 'biometrie_verifizieren')
    expect(frage?.args.nachricht).toBe('Privatkarte •••• 1111 auf shop.example in das Formular von js.stripe.com einfügen')
    expect(gefuellt().map((g) => [g.args.fuer, g.args.rahmen])).toEqual([['https://shop.example/kasse', 'https://js.stripe.com']])
  })

  it('füllt nichts, wenn Windows Hello abgelehnt wird', async () => {
    hello.bestaetigt = false
    feld('https://shop.example/kasse', 'karte')
    leiste()
    fireEvent.click(screen.getByRole('button', { name: '•••• 1111 einfügen' }))
    await warten()
    expect(gefuellt()).toEqual([])
    expect(screen.getByRole('region', { name: 'Zahlung' })).toBeInTheDocument()
  })

  it('ohne Windows Hello nur mit dem Master-Passwort', async () => {
    hello.da = false
    const stimmt = vi.fn(async (pw: string) => pw === 'richtig')
    useVaultStore.setState({ masterPasswortStimmt: stimmt })
    feld('https://shop.example/kasse', 'karte')
    leiste()
    fireEvent.click(screen.getByRole('button', { name: '•••• 1111 einfügen' }))
    const eingabe = await screen.findByLabelText('Master-Passwort')
    fireEvent.change(eingabe, { target: { value: 'falsch' } })
    fireEvent.click(screen.getByRole('button', { name: 'Bestätigen' }))
    await warten()
    expect(await screen.findByText('Das Master-Passwort stimmt nicht.')).toBeInTheDocument()
    expect(gefuellt()).toEqual([])

    fireEvent.change(screen.getByLabelText('Master-Passwort'), { target: { value: 'richtig' } })
    fireEvent.click(screen.getByRole('button', { name: 'Bestätigen' }))
    await warten()
    expect(stimmt).toHaveBeenCalledTimes(2)
    expect(gefuellt()).toHaveLength(1)
  })

  it('bietet über HTTP nichts zum Einfügen an', () => {
    feld('http://shop.example/kasse', 'karte')
    leiste()
    expect(screen.getByRole('region', { name: 'Zahlung' })).toHaveTextContent('nicht per HTTPS')
    expect(screen.queryByRole('button', { name: /einfügen/ })).not.toBeInTheDocument()
  })

  it('ein Anmeldefeld bekommt nie eine Karte, ein Kartenfeld nie ein Konto', () => {
    feld('https://shop.example/login')
    const { unmount } = leiste()
    expect(screen.getByRole('button', { name: 'Als ada einfügen' })).toBeInTheDocument()
    expect(screen.queryByText(/1111/)).not.toBeInTheDocument()
    unmount()
    feld('https://shop.example/kasse', 'konto')
    leiste()
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })
})
