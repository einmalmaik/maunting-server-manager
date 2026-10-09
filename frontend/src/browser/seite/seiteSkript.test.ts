/**
 * `seite.js` (browser/src-tauri/src) läuft in jeder Seite. Hier in jsdom mit
 * nachgebauter WebView2-Brücke: jsdom misst alles mit 0, deshalb gelten
 * Felder als 200 × 30 px groß, solange sie kein `data-flach` (0 × 0) oder
 * `data-groesse="b,h"` tragen.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

// Vitest läuft in `frontend/`.
const skript = readFileSync(resolve(process.cwd(), '../browser/src-tauri/src/seite.js'), 'utf-8')
const gesendet: unknown[] = []
let empfangen: (e: { data: unknown }) => void = () => {}

beforeAll(() => {
  const fenster = window as unknown as { chrome?: unknown }
  fenster.chrome = {
    webview: {
      postMessage: (m: string) => gesendet.push(JSON.parse(m)),
      addEventListener: (_: string, rueckruf: typeof empfangen) => (empfangen = rueckruf),
    },
  }
  HTMLElement.prototype.getClientRects = function () {
    return (this.hasAttribute('data-flach') ? [] : [{}]) as unknown as DOMRectList
  }
  HTMLElement.prototype.getBoundingClientRect = function () {
    const [breite, hoehe] = this.hasAttribute('data-flach') ? [0, 0] : (this.getAttribute('data-groesse') ?? '200,30').split(',').map(Number)
    return { x: 10, y: 10, left: 10, top: 10, right: 10 + breite, bottom: 10 + hoehe, width: breite, height: hoehe } as DOMRect
  }
  new Function(skript)()
})

const fokus = (el: Element) => el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
const wert = (sel: string) => (document.querySelector(sel) as HTMLInputElement).value

const KASSE = `
  <form id="kasse">
    <input id="email" type="email" name="email" value="ada@example.com">
    <input id="nummer" autocomplete="cc-number">
    <input id="name" name="cardholder">
    <select id="monat" autocomplete="cc-exp-month"><option value="">MM</option><option value="09">09</option><option value="12">12</option></select>
    <input id="jahr" name="exp_year" maxlength="2">
    <input id="cvc" type="password" name="cvc">
    <input id="falle" autocomplete="cc-number" style="opacity:0">
    <input id="flach" name="cvv" data-flach>
    <input id="winzig" name="card_number" data-groesse="10,8">
    <input id="iban" name="iban">
    <button type="submit">Bezahlen</button>
  </form>`

const KARTE = { nummer: '4111111111111111', inhaber: 'Ada', monat: 12, jahr: 2030, pruefnummer: '123' }

describe('Zahlungsfelder in der Seite', () => {
  beforeEach(() => {
    document.body.innerHTML = KASSE
    gesendet.length = 0
  })

  it('meldet ein Kartenfeld als Zahlung, nie als Anmeldung', () => {
    fokus(document.querySelector('#cvc')!)
    expect(gesendet).toEqual([{ t: 'zahlung', art: 'karte' }])
    fokus(document.querySelector('#iban')!)
    expect(gesendet).toContainEqual({ t: 'zahlung', art: 'konto' })
  })

  it('schickt eine Prüfnummer im Passwortfeld beim Absenden nicht als Passwort', () => {
    ;(document.querySelector('#cvc') as HTMLInputElement).value = '123'
    document.querySelector('#kasse')!.dispatchEvent(new Event('submit', { bubbles: true }))
    expect(JSON.stringify(gesendet)).not.toContain('123')
  })

  it('füllt nur sichtbare Felder der Art, nach der Anweisung des Browsers', () => {
    fokus(document.querySelector('#nummer')!)
    empfangen({ data: { t: 'fuellen', karte: KARTE } })
    expect(wert('#nummer')).toBe('4111111111111111')
    expect(wert('#name')).toBe('Ada')
    expect(wert('#monat')).toBe('12')
    expect(wert('#jahr')).toBe('30')
    expect(wert('#cvc')).toBe('123')
    // Unsichtbare Felder und andere Arten bleiben leer.
    expect(wert('#falle')).toBe('')
    expect(wert('#flach')).toBe('')
    // 10 × 8 px samt Polsterung: am Bildschirm kein erkennbares Feld (Laufzeitprobe 08.10.2026).
    expect(wert('#winzig')).toBe('')
    expect(wert('#iban')).toBe('')
    expect(wert('#email')).toBe('ada@example.com')
  })

  it('ein Konto füllt keine Kartenfelder', () => {
    fokus(document.querySelector('#iban')!)
    empfangen({ data: { t: 'fuellen', konto: { iban: 'DE89370400440532013000', inhaber: null, bic: null } } })
    expect(wert('#iban')).toBe('DE89370400440532013000')
    expect(wert('#nummer')).toBe('')
  })

  it('füllt nichts ohne ein vorher fokussiertes Zahlungsfeld der Seite', () => {
    document.body.innerHTML = KASSE
    fokus(document.querySelector('#email')!)
    // Das zuletzt fokussierte Zahlungsfeld stammt aus der alten Seite.
    empfangen({ data: { t: 'fuellen', karte: KARTE } })
    expect(wert('#nummer')).toBe('')
  })
})

describe('Neue Passwörter in der Seite', () => {
  beforeEach(() => {
    gesendet.length = 0
  })

  const feldMeldung = (sel: string) => {
    gesendet.length = 0
    const el = document.querySelector(sel) as HTMLInputElement
    el.focus()
    fokus(el)
    return gesendet.filter((m) => (m as { t: string }).t === 'feld').at(-1)
  }

  it('füllt beim Wechsel nur die neuen Felder, das bisherige Passwort bleibt, der Fokus auch', () => {
    document.body.innerHTML = `
      <form><input id="alt" type="password" autocomplete="current-password" value="Bisher-1">
      <input id="neu1" type="password"><input id="neu2" type="password"></form>
      <form><input id="alt3" type="password" value="Bisher-2"><input id="n3a" type="password"><input id="n3b" type="password"></form>`
    expect(feldMeldung('#neu1')).toEqual({ t: 'feld', passwort: true, neu: true, sicher: true })
    empfangen({ data: { t: 'fuellen', benutzer: null, passwort: null, neu: 'Erzeugt-1' } })
    expect([wert('#alt'), wert('#neu1'), wert('#neu2')]).toEqual(['Bisher-1', 'Erzeugt-1', 'Erzeugt-1'])
    expect(document.activeElement?.id).toBe('neu1')

    // Ohne Auszeichnung: von drei Feldern ist das erste das bisherige.
    feldMeldung('#n3a')
    empfangen({ data: { t: 'fuellen', benutzer: null, passwort: null, neu: 'Erzeugt-2' } })
    expect([wert('#alt3'), wert('#n3a'), wert('#n3b')]).toEqual(['Bisher-2', 'Erzeugt-2', 'Erzeugt-2'])
  })

  it('erzeugt nur bei eindeutigen Formularen von selbst', () => {
    document.body.innerHTML = `
      <form action="/session/create"><input id="login" type="password"></form>
      <form><input id="reg" type="password" autocomplete="new-password"></form>`
    expect(feldMeldung('#login')).toEqual({ t: 'feld', passwort: true, neu: true, sicher: false })
    expect(feldMeldung('#reg')).toEqual({ t: 'feld', passwort: true, neu: true, sicher: true })
  })

  it('erkennt eine Registrierung an ihrem Hauptknopf, nicht an einem zweiten Knopf', () => {
    // Aufbau wie bei Nitrado (08.10.2026): ein Passwortfeld, keine Auszeichnung, keine Adresse.
    document.body.innerHTML = `
      <form><input type="email" name="email"><input id="einzeln" type="password" name="password"><button type="submit">Registrieren</button></form>
      <form><input type="email" name="email"><input id="anmelden" type="password" name="password"><button>Anmelden</button><button>Registrieren</button></form>`
    // Gleiche Meldungen hintereinander schickt die Seite nur einmal: erst die andere.
    expect(feldMeldung('#anmelden')).toEqual({ t: 'feld', passwort: true, neu: false, sicher: false })
    expect(feldMeldung('#einzeln')).toEqual({ t: 'feld', passwort: true, neu: true, sicher: true })
  })
})

describe('Rahmen einer Kasse', () => {
  beforeEach(() => {
    gesendet.length = 0
    document.body.innerHTML = `
      <iframe id="stripe" src="https://js.stripe.com/v3/elements-inner-card.html"></iframe>
      <iframe id="unsicher" src="http://zahlung.example/feld"></iframe>
      <iframe id="winzig" src="https://werbung.example/" data-groesse="1,1"></iframe>
      <input id="email" type="email" name="email">`
  })
  const rahmen = () => gesendet.filter((m) => (m as { t?: string }).t === 'rahmen')

  it('meldet den Rahmen mit Fokus nur mit seiner Herkunft, und nur sichtbar und über HTTPS', () => {
    fokus(document.querySelector('#stripe')!)
    expect(rahmen()).toEqual([{ t: 'rahmen', herkunft: 'https://js.stripe.com' }])
    fokus(document.querySelector('#unsicher')!)
    fokus(document.querySelector('#stripe')!)
    fokus(document.querySelector('#winzig')!)
    expect(rahmen()).toEqual([
      { t: 'rahmen', herkunft: 'https://js.stripe.com' },
      { t: 'rahmen', herkunft: null },
      { t: 'rahmen', herkunft: 'https://js.stripe.com' },
      { t: 'rahmen', herkunft: null },
    ])
  })

  it('nimmt den Rahmen zurück, sobald ein Feld der Seite den Fokus hat', () => {
    fokus(document.querySelector('#stripe')!)
    fokus(document.querySelector('#email')!)
    expect(rahmen()).toEqual([
      { t: 'rahmen', herkunft: 'https://js.stripe.com' },
      { t: 'rahmen', herkunft: null },
    ])
  })
})
