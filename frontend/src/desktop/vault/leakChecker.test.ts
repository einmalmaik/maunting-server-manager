import { describe, it, expect, vi, beforeEach } from 'vitest'
import { checkPasswordLeak, createDebouncedLeakChecker } from './leakChecker'

describe('leakChecker', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('gibt false für kurze Passwörter (< 6 Zeichen) ohne Netzwerkaufruf zurück (SEC-10)', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')

    const resEmpty = await checkPasswordLeak('')
    expect(resEmpty.isLeaked).toBe(false)
    expect(resEmpty.checked).toBe(false)

    const resShort = await checkPasswordLeak('12345')
    expect(resShort.isLeaked).toBe(false)
    expect(resShort.checked).toBe(false)

    expect(fetchSpy).not.toHaveBeenCalled()
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

  it('debounced leak checker wartet 400 ms Inaktivität vor Netzwerkaufruf (SEC-10)', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      text: async () => `00000000000000000000000000000000000:0\r\n`,
    } as Response)

    let callCount = 0
    const checker = createDebouncedLeakChecker(() => {
      callCount++
    }, 400)

    // Schnell hintereinander tippen: 'secret1', 'secret12', 'secret123'
    checker('secret1')
    checker('secret12')
    checker('secret123')

    // Sofort: Noch kein Fetch aufgerufen
    expect(fetchSpy).not.toHaveBeenCalled()

    // 100 ms vergehen: immer noch nicht
    await new Promise((r) => setTimeout(r, 100))
    expect(fetchSpy).not.toHaveBeenCalled()

    // Nach Ablauf des 400ms Debounce-Intervalls: Fetch wird genau 1x aufgerufen
    await new Promise((r) => setTimeout(r, 350))
    expect(fetchSpy).toHaveBeenCalledTimes(1)
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
