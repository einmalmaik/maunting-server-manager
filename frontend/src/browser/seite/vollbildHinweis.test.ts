import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const kurzinfo = vi.fn<(blase: Record<string, unknown> | null) => Promise<null>>(() => Promise.resolve(null))
vi.mock('../services/nativ', () => ({ nativ: { kurzinfo: (b: Record<string, unknown> | null) => kurzinfo(b) } }))

import { HINWEIS_MS, hostFuerHinweis, vollbildHinweis } from './vollbildHinweis'

const zuletzt = async () => {
  await vi.waitFor(() => expect(kurzinfo).toHaveBeenCalled())
  return kurzinfo.mock.calls.at(-1)?.[0]
}

describe('Hinweis beim Vollbild', () => {
  beforeEach(() => {
    kurzinfo.mockClear()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('nennt den Host samt Port und dass Esc das Vollbild beendet', async () => {
    vollbildHinweis(true, 'https://video.example:8443/film?x=1')
    expect(await zuletzt()).toMatchObject({ text: 'video.example:8443 ist jetzt im Vollbild. Esc beendet es.', richtung: 'oben' })
  })

  it('kürzt einen langen Host vorne, das Ende nennt den Eigentümer', () => {
    const host = `${'a'.repeat(60)}.google.com.boese.example`
    expect(hostFuerHinweis(`https://${host}/`)).toBe(`…${host.slice(-48)}`)
    expect(hostFuerHinweis(`https://${host}/`).endsWith('boese.example')).toBe(true)
  })

  it('verschwindet nach einigen Sekunden und sofort, wenn das Vollbild endet', async () => {
    vi.useFakeTimers()
    vollbildHinweis(true, 'https://video.example/')
    await vi.advanceTimersByTimeAsync(HINWEIS_MS)
    expect(kurzinfo.mock.calls.at(-1)?.[0]).toBeNull()
    kurzinfo.mockClear()
    vollbildHinweis(true, 'https://video.example/')
    vollbildHinweis(false, 'https://video.example/')
    await vi.advanceTimersByTimeAsync(0)
    expect(kurzinfo.mock.calls.at(-1)?.[0]).toBeNull()
    kurzinfo.mockClear()
    await vi.advanceTimersByTimeAsync(HINWEIS_MS)
    expect(kurzinfo).not.toHaveBeenCalled()
  })
})
