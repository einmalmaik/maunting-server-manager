import { beforeEach, describe, expect, it } from 'vitest'

import type { VaultItem } from '@/desktop/vault/vaultEintrag'

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'

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
    expect(useFormulare.getState().abgeschickt[TAB]).toEqual({ url: 'https://accounts.example.com', benutzer: 'ada@example.com', passwort: 'Gipfel' })

    melden('https://a.example/', { t: 'benutzer', wert: 'ada' })
    melden('https://b.example/', { t: 'absenden', benutzer: '', passwort: 'x', neu: false })
    expect(useFormulare.getState().abgeschickt[TAB].benutzer).toBe('')
  })

  it('merkt sich bei einem Kartenfeld den Rahmen, in dem es liegt, und vergisst ihn beim nächsten Feld', () => {
    useFormulare.getState().ereignis({ art: 'formular', id: TAB, url: 'https://shop.example/', meldung: { t: 'zahlung', art: 'karte' }, rahmen: 'https://js.stripe.com' })
    expect(useFormulare.getState().feld[TAB]).toEqual({ url: 'https://shop.example/', neu: false, zahlung: 'karte', rahmen: 'https://js.stripe.com' })
    melden('https://shop.example/', { t: 'zahlung', art: 'karte' })
    expect(useFormulare.getState().feld[TAB]).toEqual({ url: 'https://shop.example/', neu: false, zahlung: 'karte' })
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

  // Bis 09.10.2026 ging eine HTTPS-Anmeldung auch in die http-Seite desselben Hosts.
  it('bietet einer Seite ohne HTTPS nur Anmeldungen an, die für http gespeichert sind', () => {
    const items = [
      eintrag({ url: 'https://bank.example/', username: 'sicher' }),
      eintrag({ url: 'bank.example', username: 'ohne-schema' }),
      eintrag({ url: 'http://bank.example/', username: 'alt' }),
    ]
    expect(anmeldungenFuer(items, 'http://bank.example/login').map((i) => i.username)).toEqual(['alt'])
    expect(anmeldungenFuer(items, 'https://bank.example/login').map((i) => i.username).sort()).toEqual(['alt', 'ohne-schema', 'sicher'])
  })

  // Bis 10.10.2026 stand die ganze Adresse samt Abfrage im Tresor, auch Token aus der Adresse.
  it('merkt sich von einer abgeschickten Anmeldung nur die Herkunft', () => {
    melden('https://konto.example:8443/login/neu?token=geheim&mail=ada%40example.com#x', { t: 'absenden', benutzer: 'ada', passwort: 'pw', neu: false })
    expect(useFormulare.getState().abgeschickt[TAB].url).toBe('https://konto.example:8443')
  })

  // Bis 10.10.2026 blieb eine abgeschickte Anmeldung über einen Kontowechsel stehen.
  it('vergisst Abgeschicktes und Erzeugtes, sobald ein anderes Konto angemeldet ist', () => {
    setzeAngemeldetesKonto(1)
    useFormulare.getState().erzeugtMerken(TAB, 'https://example.com/neu', 'Erzeugt-1')
    melden('https://example.com/login', { t: 'absenden', benutzer: 'ada', passwort: 'geheim', neu: false })
    expect(useFormulare.getState().abgeschickt[TAB]).toBeDefined()
    setzeAngemeldetesKonto(2)
    const s = useFormulare.getState()
    expect([s.abgeschickt, s.erzeugt, s.feld, s.schritt]).toEqual([{}, {}, {}, {}])
    setzeAngemeldetesKonto(null)
  })

  // Bis 10.10.2026 bekam jeder Port eines Hosts die Anmeldungen aller anderen.
  it('bietet eine Anmeldung nur unter ihrem Port an', () => {
    const items = [
      eintrag({ url: 'https://dienst.example/', username: 'standard' }),
      eintrag({ url: 'https://dienst.example:8443/', username: 'test' }),
      eintrag({ url: 'dienst.example:8443', username: 'ohne-schema' }),
    ]
    expect(anmeldungenFuer(items, 'https://www.dienst.example/login').map((i) => i.username)).toEqual(['standard'])
    expect(anmeldungenFuer(items, 'https://dienst.example:8443/login').map((i) => i.username).sort()).toEqual(['ohne-schema', 'test'])
    expect(anmeldungenFuer(items, 'https://dienst.example:9000/')).toEqual([])
    expect(anmeldungenFuer(items, 'https://dienst.example:443/').map((i) => i.username)).toEqual(['standard'])
  })

  it('fragt nicht, wenn die Anmeldung genau so im Tresor steht, und bietet sonst Aktualisieren oder Speichern', () => {
    const items = [eintrag({ url: 'https://example.com/', username: 'ada', password: 'alt' })]
    const a = { url: 'https://example.com/login', benutzer: 'ada', passwort: 'alt' }
    expect(speicherFrage(items, a)).toBeNull()
    expect(speicherFrage(items, { ...a, passwort: 'neu' })).toMatchObject({ art: 'aendern' })
    expect(speicherFrage(items, { ...a, benutzer: 'grace' })).toEqual({ art: 'neu' })
  })
})
