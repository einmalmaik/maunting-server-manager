/**
 * Escape beendet das Vollbild im Browser (`webview2.rs`) und schickt
 * `vollbild_aus` an `seite.js`, damit auch die Seite es verlässt. jsdom kennt
 * die Fullscreen-API nicht; sie wird vor dem Skript nachgebaut.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

const skript = readFileSync(resolve(process.cwd(), '../browser/src-tauri/src/seite.js'), 'utf-8')
let empfangen: (d: unknown) => void = () => {}
let element: Element | null = null
let verlassen = 0

beforeAll(() => {
  ;(window as unknown as { chrome?: unknown }).chrome = {
    webview: {
      postMessage: () => {},
      addEventListener: (_: string, rueckruf: (e: unknown) => void) => {
        empfangen = (d) => rueckruf({ data: JSON.stringify(d) })
      },
    },
  }
  Object.defineProperty(Document.prototype, 'fullscreenElement', { get: () => element, configurable: true })
  Object.defineProperty(Document.prototype, 'exitFullscreen', {
    value: () => {
      verlassen++
      element = null
      return Promise.resolve()
    },
    configurable: true,
    writable: true,
  })
  new Function(skript)()
})

beforeEach(() => {
  verlassen = 0
  element = document.body
})

describe('Vollbild in der Seite', () => {
  it('verlässt das Vollbild, wenn der Browser es sagt, auch wenn die Seite das Verlassen ersetzt hat', () => {
    Object.defineProperty(Document.prototype, 'exitFullscreen', { value: () => Promise.resolve(), configurable: true, writable: true })
    Object.defineProperty(Document.prototype, 'fullscreenElement', { get: () => null, configurable: true })
    empfangen({ t: 'vollbild_aus' })
    expect(verlassen).toBe(1)
    expect(element).toBeNull()
  })

  it('tut nichts, wenn die Seite gar nicht im Vollbild ist', () => {
    element = null
    empfangen({ t: 'vollbild_aus' })
    expect(verlassen).toBe(0)
  })
})
