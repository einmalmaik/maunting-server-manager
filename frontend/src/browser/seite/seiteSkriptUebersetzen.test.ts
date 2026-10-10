/**
 * Übersetzen in `seite.js` (browser/src-tauri/src): die Seite gibt Text nur
 * auf Anfrage heraus und bekommt nur Text zurück. In jsdom mit nachgebauter
 * WebView2-Brücke, wie `seiteSkript.test.ts`.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

const skript = readFileSync(resolve(process.cwd(), '../browser/src-tauri/src/seite.js'), 'utf-8')
type Stueck = { t: string; nr: number; sprache: string; texte: string[] }
const gesendet: Stueck[] = []
let empfangen: (e: { data: unknown }) => void = () => {}

beforeAll(() => {
  ;(window as unknown as { chrome?: unknown }).chrome = {
    webview: {
      postMessage: (m: string) => gesendet.push(JSON.parse(m)),
      // Die WebView2 schickt Text (`PostWebMessageAsString` in `formulare.rs`).
      addEventListener: (_: string, rueckruf: typeof empfangen) => (empfangen = (e) => rueckruf({ data: JSON.stringify(e.data) })),
    },
  }
  new Function(skript)()
})

const schritt = (nr: number, schritt: string, texte?: string[]) => empfangen({ data: { t: 'uebersetzen', nr, schritt, texte } })
const zuletzt = () => gesendet[gesendet.length - 1]

const SEITE = `
  <h1>Willkommen</h1>
  <p>Der <b>Browser</b> übersetzt.</p>
  <img src="data:," alt="Ein Bild">
  <input placeholder="Suchen" value="nicht der Wert">
  <script>var geheim = 'Skript'</script>
  <style>.x { content: 'Stil' }</style>
  <code>Quelltext</code>
  <textarea>Eingabe</textarea>
  <p translate="no">Markenname</p>
  <div contenteditable="true">Entwurf</div>
  <p>   </p>
  <p>42</p>`

describe('Übersetzen in der Seite', () => {
  beforeEach(() => {
    document.documentElement.setAttribute('lang', 'de-DE')
    document.body.innerHTML = SEITE
    schritt(0, 'original')
    gesendet.length = 0
  })

  it('schickt nichts ohne Anfrage und nimmt kein „weiter“ ohne Start', () => {
    schritt(1, 'weiter', ['x'])
    expect(gesendet).toEqual([])
  })

  it('gibt nur lesbaren Text heraus, keine Skripte, Eingaben oder Quelltext', () => {
    schritt(1, 'start')
    expect(zuletzt()).toEqual({
      t: 'texte',
      nr: 1,
      sprache: 'de-DE',
      texte: ['Willkommen', 'Der ', 'Browser', ' übersetzt.', 'Ein Bild', 'Suchen'],
    })
  })

  it('setzt Übersetzungen nur als Text ein, nie als HTML', () => {
    schritt(1, 'start')
    schritt(2, 'weiter', ['Welcome', 'The ', '<img src=x onerror="window.boese=1">', ' translates.', 'A picture', 'Search'])
    expect(document.querySelector('h1')!.textContent).toBe('Welcome')
    expect(document.querySelector('b')!.textContent).toBe('<img src=x onerror="window.boese=1">')
    expect(document.querySelectorAll('img')).toHaveLength(1)
    expect(document.querySelector('img')!.getAttribute('alt')).toBe('A picture')
    expect((document.querySelector('input') as HTMLInputElement).placeholder).toBe('Search')
    expect((document.querySelector('input') as HTMLInputElement).value).toBe('nicht der Wert')
    expect(zuletzt()).toEqual({ t: 'texte', nr: 2, sprache: 'de-DE', texte: [] })
  })

  it('verwirft Übersetzungen, die nicht zum letzten Stück passen', () => {
    schritt(1, 'start')
    gesendet.length = 0
    schritt(2, 'weiter', ['Welcome'])
    expect(document.querySelector('h1')!.textContent).toBe('Willkommen')
    expect(gesendet).toEqual([])
  })

  it('stellt das Original her, lässt aber stehen, was die Seite inzwischen selbst geändert hat', () => {
    schritt(1, 'start')
    schritt(2, 'weiter', ['Welcome', 'The ', 'browser', ' translates.', 'A picture', 'Search'])
    document.querySelector('b')!.firstChild!.nodeValue = 'von der Seite'
    gesendet.length = 0
    schritt(3, 'original')
    expect(document.querySelector('h1')!.textContent).toBe('Willkommen')
    expect(document.querySelector('b')!.textContent).toBe('von der Seite')
    expect(document.querySelector('img')!.getAttribute('alt')).toBe('Ein Bild')
    expect(gesendet).toEqual([])
  })

  it('gibt lange Seiten in Stücken heraus', () => {
    document.body.innerHTML = Array.from({ length: 250 }, (_, i) => `<p>Absatz ${i}</p>`).join('')
    schritt(1, 'start')
    expect(zuletzt().texte).toHaveLength(100)
    schritt(2, 'weiter', zuletzt().texte.map((t) => t.toUpperCase()))
    expect(zuletzt().texte[0]).toBe('Absatz 100')
    schritt(3, 'weiter', zuletzt().texte)
    expect(zuletzt().texte).toHaveLength(50)
    schritt(4, 'weiter', zuletzt().texte)
    expect(zuletzt().texte).toEqual([])
    expect(document.querySelector('p')!.textContent).toBe('ABSATZ 0')
  })
})
