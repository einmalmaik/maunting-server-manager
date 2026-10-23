import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const nativ = vi.hoisted(() => ({ sprachdatenLaden: vi.fn() }))
const plattform = vi.hoisted(() => ({ android: false }))
vi.mock('../services/nativ', () => ({ nativ }))
vi.mock('../services/plattform', () => ({ istAndroid: () => plattform.android }))

import { CDN, dateienVon, entfernen, fehlendeGroesse, geladen, holen, quellen, spracheVon, weg, type Datei } from './modelle'

describe('Wege zwischen Sprachen', () => {
  it('nimmt das direkte Modell oder geht über Englisch', () => {
    expect(weg('de', 'en')).toEqual(['de-en'])
    expect(weg('en', 'de')).toEqual(['en-de'])
    expect(weg('fr', 'de')).toEqual(['fr-en', 'en-de'])
    expect(weg('ja', 'de')).toEqual(['ja-en', 'en-de'])
    expect(weg('de', 'de')).toBeNull()
    expect(weg('', 'de')).toBeNull()
    expect(weg('xx', 'en')).toBeNull()
    expect(quellen('de')).toEqual(expect.arrayContaining(['fr', 'ja', 'ko', 'zh-Hans']))
    expect(quellen('de')).not.toContain('de')
  })

  it('liest die Sprache aus dem lang der Seite', () => {
    expect(spracheVon('de-DE')).toBe('de')
    expect(spracheVon('EN_us')).toBe('en')
    expect(spracheVon('zh-TW')).toBe('zh-Hant')
    expect(spracheVon('zh-Hant')).toBe('zh-Hant')
    expect(spracheVon('zh-CN')).toBe('zh-Hans')
    expect(spracheVon('zh')).toBe('zh-Hans')
    expect(spracheVon('zh-SG')).toBe('zh-Hans')
    expect(spracheVon('ja-JP')).toBe('ja')
    expect(spracheVon('ko-KR')).toBe('ko')
    expect(spracheVon('no')).toBe('nb')
    expect(spracheVon('tlh')).toBe('')
    expect(spracheVon('')).toBe('')
  })

  it('nimmt auf Android die dort angebotene, kleinere Fassung', () => {
    plattform.android = false
    expect(dateienVon('ja-en').dateien.model?.groesse).toBe(59_504_955)
    plattform.android = true
    expect(dateienVon('ja-en').dateien.model?.groesse).toBe(43_977_787)
    plattform.android = false
  })
})

describe('Engine in der Fassung von Firefox', () => {
  it('liegt unverändert und mit Lizenz im Repo', () => {
    const ordner = resolve(process.cwd(), 'src/browser/uebersetzung/bergamot')
    const skript = readFileSync(resolve(process.cwd(), '../browser/scripts/uebersetzung-holen.mjs'), 'utf-8')
    const sha = (datei: string) => createHash('sha256').update(readFileSync(resolve(ordner, datei))).digest('hex')
    expect(skript).toContain(`GLUE_SHA256 = '${sha('bergamot-translator.js')}'`)
    expect(skript).toContain(`LIZENZ_SHA256 = '${sha('LICENSE')}'`)
  })
})

describe('Laden und Prüfen', () => {
  const inhalt = new TextEncoder().encode('Sprachdaten')
  const datei: Datei = { ort: 'test/modell.bin', groesse: inhalt.byteLength, sha256: createHash('sha256').update(inhalt).digest('hex') }
  const text = (b: ArrayBuffer) => new TextDecoder().decode(b)
  let ablage: Map<string, Response>
  let geholt: string[]

  beforeEach(() => {
    ablage = new Map()
    geholt = []
    vi.stubGlobal('caches', {
      open: async () => ({
        match: async (url: string) => ablage.get(url)?.clone(),
        put: async (url: string, r: Response) => void ablage.set(url, r),
        delete: async (url: string) => ablage.delete(url),
        keys: async () => [...ablage.keys()].map((url) => ({ url })),
      }),
    })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    plattform.android = false
    nativ.sprachdatenLaden.mockReset()
  })

  const netz = (bytes: Uint8Array) =>
    nativ.sprachdatenLaden.mockImplementation(async (ort: string, fortschritt: (n: number) => void) => {
      geholt.push(ort)
      fortschritt(bytes.byteLength)
      return bytes.slice().buffer
    })

  it('lädt, prüft und behält nur, was passt', async () => {
    netz(inhalt)
    const fortschritt = vi.fn()
    expect(text(await holen(datei, fortschritt))).toBe('Sprachdaten')
    expect(geholt).toEqual([datei.ort])
    expect(fortschritt).toHaveBeenCalledWith(inhalt.byteLength)
    // Beim zweiten Mal aus dem Cache.
    await holen(datei)
    expect(geholt).toHaveLength(1)
  })

  it('verwirft eine Datei mit falscher Prüfsumme und legt sie nicht ab', async () => {
    netz(new TextEncoder().encode('Sprachdatem'))
    await expect(holen(datei)).rejects.toThrow('pruefsumme')
    expect(ablage.size).toBe(0)
  })

  it('meldet, wenn Rust eine Datei wegen ihrer Größe ablehnt, und sonst einen Netzfehler', async () => {
    nativ.sprachdatenLaden.mockRejectedValueOnce('groesse').mockRejectedValueOnce('netz')
    await expect(holen(datei)).rejects.toThrow('pruefsumme')
    await expect(holen(datei)).rejects.toThrow('netz')
    expect(ablage.size).toBe(0)
  })

  it('holt neu, was im Cache nicht mehr stimmt', async () => {
    ablage.set(CDN + datei.ort, new Response(new TextEncoder().encode('verdorben!!')))
    netz(inhalt)
    expect(text(await holen(datei))).toBe('Sprachdaten')
    expect(geholt).toHaveLength(1)
  })

  it('zählt, was noch fehlt', async () => {
    const gesamt = await fehlendeGroesse(['de-en'])
    expect(gesamt).toBeGreaterThan(30_000_000)
  })

  it('zählt und entfernt auf Android die Android-Fassung', async () => {
    plattform.android = true
    const { dateien } = dateienVon('ja-en')
    for (const d of Object.values(dateien)) ablage.set(CDN + d.ort, new Response(''))
    const groesse = Object.values(dateien).reduce((n, d) => n + d.groesse, 0)
    expect(await geladen()).toContainEqual({ paar: 'ja-en', groesse })
    await entfernen('ja-en')
    expect(ablage.size).toBe(0)
    plattform.android = false
  })
})
