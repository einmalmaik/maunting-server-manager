/**
 * Speichern bei gesperrtem Tresor: der Browser legt mit dem öffentlichen
 * Schlüssel und seinem Geräteschlüssel ab, und die Übernahme nimmt es an.
 */
import 'fake-indexeddb/auto'
import { IDBFactory } from 'fake-indexeddb'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { generateHybridKeyPair } from '@msdis/shield/post-quantum'

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { eingangOeffnen } from '@/desktop/vault/tresorEingang'
import { useVaultStore } from '@/desktop/vault/vaultStore'

const { abgelegt, spkis } = vi.hoisted(() => ({ abgelegt: [] as { bucket: string; id: string; text: string }[], spkis: [] as { geraet: string; spki: string }[] }))

vi.mock('@/desktop/vault/tresorBlobApi', async (original) => ({
  ...(await original<object>()),
  eingangAblegen: vi.fn(async (bucket: string, id: string, text: string) => void abgelegt.push({ bucket, id, text })),
}))

const paar = await generateHybridKeyPair()
vi.mock('@/desktop/vault/tresorEingang', async (original) => ({
  ...(await original<object>()),
  posteingangEinrichten: vi.fn(async (_bucket: string, geraet: string, spki: (g: string) => Promise<string>) => {
    spkis.push({ geraet, spki: await spki(geraet) })
    return { id: 'paar-1', pqPublicKey: paar.pqPublicKey, rsaPublicKey: paar.rsaPublicKey }
  }),
}))

const { einrichten, eingerichtet, speichern } = await import('./tresorGesperrt')

describe('Speichern bei gesperrtem Tresor', () => {
  beforeEach(() => {
    globalThis.indexedDB = new IDBFactory()
    abgelegt.length = 0
    spkis.length = 0
    setzeAngemeldetesKonto(1)
    useVaultStore.setState({ isUnlocked: true, bucketId: 'bucket-1', syncWithServer: vi.fn(async () => {}) } as never)
  })
  afterEach(() => setzeAngemeldetesKonto(null))

  it('legt ab, was die Übernahme mit dem Gerät öffnet, ohne einen geheimen Schlüssel des Tresors', async () => {
    expect(await eingerichtet()).toBe(false)
    await expect(speichern('https://example.com/', 'ada', 'pw')).rejects.toThrow('nicht_eingerichtet')
    expect(await einrichten()).toBe(true)

    useVaultStore.setState({ isUnlocked: false, userKey: null })
    await speichern('https://example.com/login', 'ada', 'Gipfel-2026!')
    const [{ bucket, id, text }] = abgelegt
    expect(bucket).toBe('bucket-1')
    expect(text).not.toContain('Gipfel')

    const schluessel = [{ art: 'posteingang' as const, id: 'paar-1', ...paar }]
    const geraet = [{ art: 'geraet' as const, ...spkis[0] }]
    const geoeffnet = await eingangOeffnen(text, bucket, id, schluessel, geraet)
    expect(geoeffnet).toMatchObject({ ergebnis: 'ok', inhalt: { art: 'zugang', url: 'https://example.com/login', benutzer: 'ada', passwort: 'Gipfel-2026!' } })
    // An eine andere Kennung gebunden, gilt der Datensatz nicht.
    expect(await eingangOeffnen(text, bucket, crypto.randomUUID(), schluessel, geraet)).toEqual({ ergebnis: 'verwerfen' })
  })

  it('gehört dem Konto: ein anderes Konto oder das Abmelden nimmt das Gerät mit', async () => {
    await einrichten()
    setzeAngemeldetesKonto(2)
    expect(await eingerichtet()).toBe(false)
    setzeAngemeldetesKonto(1)
    expect(await eingerichtet()).toBe(false)
  })
})
