import { describe, it, expect, vi, beforeEach } from 'vitest'
import { checkPasswordLeak } from './leakChecker'

describe('leakChecker', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('gibt false für leere Passwörter zurück', async () => {
    const result = await checkPasswordLeak('')
    expect(result.isLeaked).toBe(false)
    expect(result.checked).toBe(false)
  })

  it('erkennt ein geleaktes Passwort anhand des SHA-1 Suffixes', async () => {
    // SHA-1 von 'password' ist 5BAA61E4C9B93F3F0682250B6CF8331B7EE68FD8
    // Prefix: 5BAA6, Suffix: 1E4C9B93F3F0682250B6CF8331B7EE68FD8
    const mockResponse = `0018A45C4D637E4C4B6445FF3F2A3F3A4B5:12\r\n1E4C9B93F3F0682250B6CF8331B7EE68FD8:3861493\r\n`

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      text: async () => mockResponse,
    } as Response)

    const result = await checkPasswordLeak('password')
    expect(result.isLeaked).toBe(true)
    expect(result.count).toBe(3861493)
    expect(result.checked).toBe(true)
  })

  it('gibt false zurück, wenn der Suffix nicht in der Liste vorkommt', async () => {
    const mockResponse = `0018A45C4D637E4C4B6445FF3F2A3F3A4B5:12\r\n`

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      text: async () => mockResponse,
    } as Response)

    const result = await checkPasswordLeak('super-geheimes-nicht-geleaktes-passwort-2026')
    expect(result.isLeaked).toBe(false)
    expect(result.count).toBe(0)
    expect(result.checked).toBe(true)
  })

  it('fängt Netzwerkfehler sauber ab und blockiert den Benutzer nicht', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Network error'))

    const result = await checkPasswordLeak('irgendein-passwort-offline')
    expect(result.isLeaked).toBe(false)
    expect(result.checked).toBe(false)
  })
})
