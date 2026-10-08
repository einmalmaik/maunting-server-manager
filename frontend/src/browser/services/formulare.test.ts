import { beforeEach, describe, expect, it } from 'vitest'

import type { VaultItem } from '@/desktop/vault/vaultEintrag'

import { anmeldungenFuer, speicherFrage, useFormulare } from './formulare'

const TAB = 'tab-a'
const melden = (url: string, meldung: Parameters<ReturnType<typeof useFormulare.getState>['ereignis']>[0]['meldung']) =>
  useFormulare.getState().ereignis({ art: 'formular', id: TAB, url, meldung })

function eintrag(teil: Partial<VaultItem>): VaultItem {
  return { id: crypto.randomUUID(), service: 'x', username: '', password: 'pw', createdAt: 0, updatedAt: 0, revision: 1, ...teil }
}

describe('Anmeldefelder im Tab', () => {
  beforeEach(() => useFormulare.setState({ feld: {}, abgeschickt: {}, schritt: {} }))

  it('nimmt bei mehrstufiger Anmeldung den Benutzer aus dem ersten Schritt, nur auf demselben Host', () => {
    melden('https://accounts.example.com/signin', { t: 'benutzer', wert: 'ada@example.com' })
    melden('https://accounts.example.com/pw', { t: 'absenden', benutzer: '', passwort: 'Gipfel', neu: false })
    expect(useFormulare.getState().abgeschickt[TAB]).toEqual({ url: 'https://accounts.example.com/pw', benutzer: 'ada@example.com', passwort: 'Gipfel' })

    melden('https://a.example/', { t: 'benutzer', wert: 'ada' })
    melden('https://b.example/', { t: 'absenden', benutzer: '', passwort: 'x', neu: false })
    expect(useFormulare.getState().abgeschickt[TAB].benutzer).toBe('')
  })

  it('vergisst beim Laden einer neuen Seite das Angebot zum Einfügen, nicht die Frage zum Speichern', () => {
    melden('https://example.com/login', { t: 'absenden', benutzer: 'ada', passwort: 'x', neu: false })
    melden('https://example.com/start', { t: 'feld', passwort: true, neu: false })
    useFormulare.getState().laedt(TAB)
    expect(useFormulare.getState().feld[TAB]).toBeUndefined()
    expect(useFormulare.getState().abgeschickt[TAB]).toBeDefined()
  })

  it('bietet nur Anmeldungen desselben Hosts an, ohne Papierkorb', () => {
    const items = [
      eintrag({ url: 'https://www.example.com/', username: 'ada' }),
      eintrag({ url: 'https://example.com.evil.test/', username: 'mallory' }),
      eintrag({ url: 'https://example.com/', username: 'alt', trashedAt: 1 }),
      eintrag({ url: 'https://example.com/', username: 'datei', category: 'datei' }),
    ]
    expect(anmeldungenFuer(items, 'https://example.com/login').map((i) => i.username)).toEqual(['ada'])
  })

  it('fragt nicht, wenn die Anmeldung genau so im Tresor steht, und bietet sonst Aktualisieren oder Speichern', () => {
    const items = [eintrag({ url: 'https://example.com/', username: 'ada', password: 'alt' })]
    const a = { url: 'https://example.com/login', benutzer: 'ada', passwort: 'alt' }
    expect(speicherFrage(items, a)).toBeNull()
    expect(speicherFrage(items, { ...a, passwort: 'neu' })).toMatchObject({ art: 'aendern' })
    expect(speicherFrage(items, { ...a, benutzer: 'grace' })).toEqual({ art: 'neu' })
  })
})
