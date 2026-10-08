/**
 * `seite.js` unter Android: die Brücke heißt dort `msbKanal`
 * (`addWebMessageListener` in `Tab.kt`) und liefert Nachrichten als Text.
 * Eigene Datei, weil das Skript je Seite einmal läuft und die Windows-Tests
 * es schon mit `chrome.webview` gestartet haben.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, describe, expect, it, vi } from 'vitest'

const skript = readFileSync(resolve(process.cwd(), '../browser/src-tauri/src/seite.js'), 'utf-8')
const gesendet: unknown[] = []
let empfangen: (e: { data: unknown }) => void = () => {}
const abgebrochen = vi.fn()

/** Eine Datei der Seite in zwei Stücken von zusammen 300 KiB. */
function dateiAntwort() {
  const stuecke = [new Uint8Array(200 * 1024).fill(1), new Uint8Array(100 * 1024).fill(2)]
  return {
    body: {
      getReader: () => ({
        read: async () => (stuecke.length ? { done: false, value: stuecke.shift() } : { done: true, value: undefined }),
        cancel: abgebrochen,
      }),
    },
  }
}

beforeAll(() => {
  ;(window as unknown as { msbKanal?: unknown }).msbKanal = {
    postMessage: (m: string) => gesendet.push(JSON.parse(m)),
    addEventListener: (_: string, rueckruf: typeof empfangen) => (empfangen = rueckruf),
  }
  HTMLElement.prototype.getClientRects = function () {
    return [{}] as unknown as DOMRectList
  }
  HTMLElement.prototype.getBoundingClientRect = function () {
    return { x: 10, y: 10, left: 10, top: 10, right: 210, bottom: 40, width: 200, height: 30 } as DOMRect
  }
  window.fetch = vi.fn(async () => dateiAntwort()) as unknown as typeof fetch
  new Function(skript)()
})

const warten = () => new Promise((r) => setTimeout(r, 0))
const teile = () => gesendet.filter((m) => (m as { t?: string }).t === 'teil') as Record<string, unknown>[]

describe('seite.js unter Android', () => {
  it('nimmt der Seite den Kanal weg, meldet darüber und füllt auf eine Textnachricht', () => {
    expect('msbKanal' in window).toBe(false)

    document.body.innerHTML = `<form><input id="name" name="email"><input id="pw" type="password"><button>Anmelden</button></form>`
    const pw = document.querySelector('#pw') as HTMLInputElement
    pw.focus()
    pw.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
    expect(gesendet).toContainEqual(expect.objectContaining({ t: 'feld', passwort: true }))

    empfangen({ data: JSON.stringify({ t: 'fuellen', benutzer: 'ada@example.com', passwort: 'Geheim-1' }) })
    expect((document.querySelector('#name') as HTMLInputElement).value).toBe('ada@example.com')
    expect(pw.value).toBe('Geheim-1')

    // Kaputter Text füllt nichts und wirft nicht.
    expect(() => empfangen({ data: '{kaputt' })).not.toThrow()
  })

  it('gibt eine blob-Datei in Teilen weiter, jeden erst nach der Quittung für den vorigen', async () => {
    expect(gesendet[0]).toEqual({ t: 'da' })
    document.body.innerHTML = `<a href="blob:https://seite.example/1" download="bericht.csv">x</a>`
    gesendet.length = 0

    empfangen({ data: JSON.stringify({ t: 'datei', nr: 7, url: 'blob:https://seite.example/1' }) })
    await warten()
    // 200 KiB passen in einen Teil; der zweite wartet auf die Quittung.
    expect(teile()).toHaveLength(1)
    expect(teile()[0]).toMatchObject({ nr: 7, name: 'bericht.csv' })
    expect(atob(teile()[0].daten as string)).toHaveLength(200 * 1024)

    empfangen({ data: JSON.stringify({ t: 'weiter', nr: 7, ok: true }) })
    await warten()
    expect(teile()).toHaveLength(2)
    empfangen({ data: JSON.stringify({ t: 'weiter', nr: 7, ok: true }) })
    await warten()
    expect(teile()[2]).toMatchObject({ nr: 7, ende: true })
  })

  it('hört auf, wenn der Browser die Datei abbricht', async () => {
    gesendet.length = 0
    empfangen({ data: JSON.stringify({ t: 'datei', nr: 8, url: 'data:text/plain,x' }) })
    await warten()
    empfangen({ data: JSON.stringify({ t: 'weiter', nr: 8, ok: false }) })
    await warten()
    expect(teile()).toHaveLength(1)
    expect(abgebrochen).toHaveBeenCalled()
  })
})
