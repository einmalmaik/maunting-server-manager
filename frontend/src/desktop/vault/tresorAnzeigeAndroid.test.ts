/**
 * „Auf dem Gerät speichern“ unter Android: die Kamera-Sicherung nimmt auch
 * Downloads mit. Ein Foto, das aus dem Tresor kommt, darf nicht als Kopie
 * wieder hochgehen; der Job erfährt deshalb seine Prüfsumme.
 */
import { createHash } from 'node:crypto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const inDerAppSpeichern = vi.hoisted(() =>
  vi.fn(async (_name: string, teile: AsyncIterable<Uint8Array>) => {
    for await (const teil of teile) void teil
    return true
  }),
)
vi.mock('@/lib/geraetSpeichern', () => ({ inDerAppSpeichern }))
vi.mock('@/services/passkeyService', () => ({ inDerApp: () => true }))
vi.mock('./tresorDateien', () => ({
  ansichtOeffnen: vi.fn(),
  ansichtSchliessen: vi.fn(),
  klartextTeile: async function* () {
    yield new TextEncoder().encode('Foto vom ')
    yield new TextEncoder().encode('Gipfel')
  },
}))
const kameraGespeichert = vi.hoisted(() => vi.fn(async (_sha: string) => undefined))
vi.mock('./kameraSicherung', () => ({ androidApp: () => true, kameraGespeichert }))

import { aufGeraetSpeichern } from './tresorAnzeige'
import { useVaultStore } from './vaultStore'
import type { BlobKopf } from './tresorDatei'

const kopf = { id: 'a'.repeat(32), groesse: 65536, echt: 15, schluessel: 'x', loeschen: 'y' } as BlobKopf
const userKey = {} as CryptoKey

describe('aufGeraetSpeichern unter Android', () => {
  beforeEach(() => {
    kameraGespeichert.mockClear()
    useVaultStore.setState({ userKey })
  })

  it('meldet die Prüfsumme eines gespeicherten Fotos an die Kamera-Sicherung', async () => {
    expect(await aufGeraetSpeichern(kopf, 'e1', userKey, 'Gipfel.jpg', 'image/jpeg')).toBe(true)
    expect(kameraGespeichert).toHaveBeenCalledWith(createHash('sha256').update('Foto vom Gipfel').digest('hex'))
  })

  it('rechnet bei Dateien, die die Sicherung nie nimmt, nichts', async () => {
    expect(await aufGeraetSpeichern(kopf, 'e1', userKey, 'Vertrag.pdf', 'application/pdf')).toBe(true)
    expect(kameraGespeichert).not.toHaveBeenCalled()
  })

  it('meldet nichts, wenn das Speichern abbricht', async () => {
    inDerAppSpeichern.mockResolvedValueOnce(false)
    expect(await aufGeraetSpeichern(kopf, 'e1', userKey, 'Gipfel.jpg', 'image/jpeg')).toBe(false)
    expect(kameraGespeichert).not.toHaveBeenCalled()
  })
})
