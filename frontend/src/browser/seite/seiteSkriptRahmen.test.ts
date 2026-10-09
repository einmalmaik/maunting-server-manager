/**
 * `seite.js` in einem Unterrahmen, etwa im Kartenfeld von Stripe. Eigene
 * Datei, weil das Skript je Dokument einmal läuft; der Rahmen entsteht, indem
 * `window.top` ein anderes Fenster nennt.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

const skript = readFileSync(resolve(process.cwd(), '../browser/src-tauri/src/seite.js'), 'utf-8')
const gesendet: unknown[] = []
let empfangen: (e: { data: unknown }) => void = () => {}

beforeAll(() => {
  ;(window as unknown as { chrome?: unknown }).chrome = {
    webview: {
      postMessage: (m: string) => gesendet.push(JSON.parse(m)),
      addEventListener: (_: string, rueckruf: typeof empfangen) => (empfangen = rueckruf),
    },
  }
  HTMLElement.prototype.getClientRects = function () {
    return [{}] as unknown as DOMRectList
  }
  HTMLElement.prototype.getBoundingClientRect = function () {
    return { x: 10, y: 10, left: 10, top: 10, right: 210, bottom: 40, width: 200, height: 30 } as DOMRect
  }
  // Ein Fenster, dessen `top` ein anderes ist; alles andere ist das echte.
  const rahmen = new Proxy(window, {
    get: (ziel, name) => {
      if (name === 'top') return {}
      const wert = Reflect.get(ziel, name, ziel)
      return typeof wert === 'function' ? wert.bind(ziel) : wert
    },
  })
  new Function('window', skript)(rahmen)
})

const fokus = (el: Element) => el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
const wert = (sel: string) => (document.querySelector(sel) as HTMLInputElement).value
const KARTE = { nummer: '4111111111111111', inhaber: 'Ada', monat: 12, jahr: 2030, pruefnummer: '123' }

beforeEach(() => {
  gesendet.length = 0
  document.body.innerHTML = `
    <input id="email" type="email" name="email">
    <input id="pw" type="password" name="password">
    <input id="nummer" autocomplete="cc-number">
    <input id="cvc" name="cvc">`
})

describe('seite.js in einem Rahmen', () => {
  it('meldet nur Zahlungsfelder, keine Anmeldung', () => {
    fokus(document.querySelector('#email')!)
    fokus(document.querySelector('#pw')!)
    expect(gesendet).toEqual([])
    fokus(document.querySelector('#nummer')!)
    expect(gesendet).toEqual([{ t: 'zahlung', art: 'karte' }])
  })

  it('füllt nur, wenn der Browser genau seine Herkunft nennt', () => {
    empfangen({ data: { t: 'fuellen', karte: KARTE, herkunft: 'https://anderer.example' } })
    empfangen({ data: { t: 'fuellen', karte: KARTE } })
    expect(wert('#nummer')).toBe('')
    // Jedes Feld des Rahmens, auch ohne Fokus: bei Adyen liegt jedes in einem eigenen.
    empfangen({ data: { t: 'fuellen', karte: KARTE, herkunft: location.origin } })
    expect(wert('#nummer')).toBe('4111111111111111')
    expect(wert('#cvc')).toBe('123')
  })

  it('füllt in einem Rahmen keine Anmeldung', () => {
    fokus(document.querySelector('#pw')!)
    empfangen({ data: { t: 'fuellen', benutzer: 'ada', passwort: 'geheim', neu: null, herkunft: location.origin } })
    expect(wert('#email')).toBe('')
    expect(wert('#pw')).toBe('')
  })
})
