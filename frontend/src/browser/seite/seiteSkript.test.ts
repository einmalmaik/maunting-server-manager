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
