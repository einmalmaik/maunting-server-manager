import { beforeEach, describe, expect, it, vi } from 'vitest'

import { melde, planeMeldung } from './hub'
import { vergissEinmalig } from './einmalig'

const mocks = vi.hoisted(() => ({
  chime: vi.fn(),
  toast: vi.fn(),
  system: vi.fn(),
}))

vi.mock('@/stores/messengerNotificationStore', () => ({ playNotificationChime: mocks.chime }))
vi.mock('@/stores/toastStore', () => ({ toast: { success: mocks.toast } }))
vi.mock('@/lib/benachrichtigung', () => ({
  sendeGeraeteBenachrichtigung: mocks.system,
}))


const ICH = 6
const PARTNER = 5

const lage = (teile: Partial<Parameters<typeof planeMeldung>[0]> = {}) => ({
  art: 'chat' as const,
  schalterAn: true,
  absenderId: PARTNER,
  eigeneId: ICH,
  empfaengerId: ICH,
  istSteuerung: false,
  stumm: false,
  blockiert: false,
  chatOffen: false,
  fensterImVordergrund: false,
  ...teile,
})

const text = (teile: Partial<{ schluessel: string; titel: string; text: string; toastText: string }> = {}) => ({
  titel: 'Neue Nachricht',
  text: 'owner',
  ...teile,
})

describe('planeMeldung', () => {
  it('meldet eine fremde Nachricht im Hintergrund auf allen Wegen', () => {
    expect(planeMeldung(lage())).toEqual({
      zaehlen: true,
      ton: true,
      toast: true,
      system: true,
      grund: null,
    })
  })

  it('zeigt keine Systemmeldung, wenn ein anderer Client der Zusteller ist', () => {
    // Zwei Fenster oder MSS und Pontis auf einem Gerät hören dasselbe
    // Ereignis. Der Server wählt einen Zusteller; der andere bleibt still,
    // zählt aber weiter und klingelt.
    const plan = planeMeldung(lage({ meldenErlaubt: false }))
    expect(plan.system).toBe(false)
    expect(plan.zaehlen).toBe(true)
    expect(plan.ton).toBe(true)
  })

  it('zeigt im Vordergrund keinen Systemhinweis, aber Ton und Einblendung', () => {
    // Negativtest: Der Mensch sieht das Fenster bereits; ein Toast des
    // Betriebssystems wäre der zweite Hinweis auf dieselbe Sache.
    const plan = planeMeldung(lage({ fensterImVordergrund: true }))
    expect(plan.system).toBe(false)
    expect(plan.ton).toBe(true)
    expect(plan.toast).toBe(true)
  })

  it('schweigt völlig, wenn der Schalter aus ist', () => {
    expect(planeMeldung(lage({ schalterAn: false }))).toEqual({
      zaehlen: false,
      ton: false,
      toast: false,
      system: false,
      grund: 'schalter-aus',
    })
  })

  it('meldet die eigene Nachricht nicht — auch nicht über einen KI-Akteur', () => {
    expect(planeMeldung(lage({ absenderId: ICH })).grund).toBe('eigene-aktion')
    expect(planeMeldung(lage({ absenderId: 'ai:6' })).grund).toBe('eigene-aktion')
  })

  it('meldet Steuerumschläge nicht', () => {
    // Quittungen sind der häufigste Umschlag überhaupt. Eine Meldung dafür
    // wäre bei jeder gelesenen Nachricht ein Ton.
    expect(planeMeldung(lage({ istSteuerung: true })).grund).toBe('steuerung')
  })

  it('meldet nichts, was einem anderen Konto gehört', () => {
    expect(planeMeldung(lage({ empfaengerId: 99 })).grund).toBe('nicht-empfaenger')
  })

  it('lässt einen stummen Chat stumm — ohne Ton und ohne Systemmeldung', () => {
    expect(planeMeldung(lage({ stumm: true }))).toEqual({
      zaehlen: false,
      ton: false,
      toast: false,
      system: false,
      grund: 'stumm',
    })
  })

  it('meldet von einem blockierten Kontakt nichts', () => {
    expect(planeMeldung(lage({ blockiert: true })).grund).toBe('blockiert')
  })

  it('meldet im offenen Chat nichts, auch keinen Zähler', () => {
    expect(planeMeldung(lage({ chatOffen: true }))).toEqual({
      zaehlen: false,
      ton: false,
      toast: false,
      system: false,
      grund: 'chat-offen',
    })
  })

  it('klingelt bei einem Anruf, aber ohne Einblendung', () => {
    const plan = planeMeldung(lage({ art: 'anruf' }))
    expect(plan.ton).toBe(true)
    expect(plan.toast).toBe(false)
    expect(plan.system).toBe(true)
  })

  it('zeigt einen Erfolg nur im Interface, ohne Ton und ohne Systemmeldung', () => {
    expect(planeMeldung(lage({ art: 'erfolg' }))).toEqual({
      zaehlen: false,
      ton: false,
      toast: true,
      system: false,
      grund: null,
    })
  })

  it('meldet einen Termin auch bei offenem Fenster', () => {
    // Anders als eine Chat-Nachricht steht ein fälliger Termin nirgends sonst
    // im Bild; er muss auch dann kommen, wenn jemand hinsieht.
    const plan = planeMeldung(lage({ art: 'termin', fensterImVordergrund: true }))
    expect(plan.system).toBe(true)
  })
})

describe('melde', () => {
  beforeEach(() => {
    mocks.chime.mockClear()
    mocks.toast.mockClear()
    mocks.system.mockReset()
    mocks.system.mockResolvedValue({ weg: 'tauri', ok: true })
    localStorage.clear()
    vergissEinmalig()
  })

  it('führt genau einen Weg aus und meldet den Plan zurück', async () => {
    const ergebnis = await melde(lage(), text())
    expect(ergebnis.doppelt).toBe(false)
    expect(ergebnis.plan.ton).toBe(true)
    expect(mocks.chime).toHaveBeenCalledTimes(1)
    expect(mocks.toast).toHaveBeenCalledTimes(1)
    expect(mocks.system).toHaveBeenCalledTimes(1)
  })

  it('zeigt dasselbe Ereignis in zwei Fenstern nur einmal', async () => {
    // Zwei Tabs, derselbe Umschlag: bis 22.10.2026 kam der Ton doppelt.
    const erste = await melde(lage(), text({ schluessel: 'chat:mb-1:4711' }))
    const zweite = await melde(lage(), text({ schluessel: 'chat:mb-1:4711' }))
    expect(erste.doppelt).toBe(false)
    expect(zweite.doppelt).toBe(true)
    expect(mocks.chime).toHaveBeenCalledTimes(1)
    expect(mocks.toast).toHaveBeenCalledTimes(1)
    expect(mocks.system).toHaveBeenCalledTimes(1)
  })

  it('verbraucht nichts, was ohnehin nicht gemeldet wird', async () => {
    // Sonst ginge die Meldung des zweiten Fensters verloren, obwohl das erste
    // sie nur wegen Stummschaltung verworfen hat.
    const stumm = await melde(lage({ stumm: true }), text({ schluessel: 'chat:mb-1:4711' }))
    expect(stumm.plan.grund).toBe('stumm')
    const wach = await melde(lage(), text({ schluessel: 'chat:mb-1:4711' }))
    expect(wach.plan.grund).toBeNull()
    expect(mocks.chime).toHaveBeenCalledTimes(1)
  })

  it('verschweigt eine gescheiterte Systemmeldung nicht', async () => {
    const warnung = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mocks.system.mockResolvedValue({ weg: 'keiner', ok: false, fehler: 'kein Weg verfügbar' })
    const ergebnis = await melde(lage(), text({ schluessel: 'chat:mb-1:4712' }))
    expect(ergebnis.system).toEqual({ weg: 'keiner', ok: false, fehler: 'kein Weg verfügbar' })
    expect(warnung).toHaveBeenCalled()
    warnung.mockRestore()
  })

  it('bleibt stehen, wenn der Darstellungsweg wirft, und sagt warum', async () => {
    // Ein Client ohne Push ist kein Grund, den Menschen gar nicht zu wecken —
    // und eine Ausnahme im Ereignis-Handler wäre ein Fehler ohne Adresse.
    const warnung = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mocks.system.mockRejectedValue(new Error('kaputt'))
    const ergebnis = await melde(lage(), text({ schluessel: 'chat:mb-1:4713' }))
    expect(ergebnis.system).toEqual({ weg: 'keiner', ok: false, fehler: 'kaputt' })
    expect(mocks.chime).toHaveBeenCalledTimes(1)
    expect(warnung).toHaveBeenCalled()
    warnung.mockRestore()
  })
})
