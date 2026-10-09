import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { kuerzelAusTaste } = await import('./kuerzel')

const rust = readFileSync(resolve(process.cwd(), '../browser/src-tauri/src/tabs/desktop/webview2.rs'), 'utf-8')
const TASTEN: Record<string, string> = { TAB: 'Tab', F3: 'F3', F6: 'F6', F7: 'F7', F12: 'F12', KOMMA: ',' }

/** Jede Taste aus `kuerzel` in `webview2.rs` als KeyboardEvent mit dem Namen, den Rust meldet. */
function ausRust(): { taste: KeyboardEvent; name: string }[] {
  const rumpf = rust.slice(rust.indexOf('pub(crate) fn kuerzel('), rust.indexOf('_ => None', rust.indexOf('pub(crate) fn kuerzel(')))
  const faelle: { taste: KeyboardEvent; name: string }[] = []
  for (const zeile of rumpf.split('\n')) {
    const name = zeile.match(/=> Some\("(\w+)"\)/)?.[1]
    if (!name) continue
    for (const [, strg, umschalt, alt, buchstaben, konstante] of zeile.matchAll(/\((true|false), (true|false), (true|false), (?:None|Some\(([^)]*)\)), (\w+)\)/g)) {
      const tasten = buchstaben ? [...buchstaben.matchAll(/'(\w)'/g)].map((b) => b[1]) : [TASTEN[konstante]]
      for (const key of tasten) {
        faelle.push({ name, taste: new KeyboardEvent('keydown', { key, ctrlKey: strg === 'true', shiftKey: umschalt === 'true', altKey: alt === 'true' }) })
      }
    }
  }
  return faelle
}

describe('kuerzelAusTaste', () => {
  it('ordnet jede Taste zu wie Rust', () => {
    const faelle = ausRust()
    expect(faelle.length).toBeGreaterThan(20)
    for (const { taste, name } of faelle) {
      expect([taste.key, taste.ctrlKey, taste.shiftKey, taste.altKey, kuerzelAusTaste(taste)]).toEqual([taste.key, taste.ctrlKey, taste.shiftKey, taste.altKey, name])
    }
  })

  it('kennt Neuladen nur in der Oberfläche', () => {
    expect(kuerzelAusTaste(new KeyboardEvent('keydown', { key: 'r', ctrlKey: true }))).toBe('neu_laden')
    expect(kuerzelAusTaste(new KeyboardEvent('keydown', { key: 'F5' }))).toBe('neu_laden')
    expect(rust).not.toContain('"neu_laden"')
  })

  it('lässt der Oberfläche gewöhnliche Tasten', () => {
    expect(kuerzelAusTaste(new KeyboardEvent('keydown', { key: 'a' }))).toBeNull()
    expect(kuerzelAusTaste(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true }))).toBeNull()
  })
})
