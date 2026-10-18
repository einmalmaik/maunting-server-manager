import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import i18n from '@/i18n'
import { api, SanitizedApiError } from './client'

beforeEach(async () => {
  await i18n.changeLanguage('en')
})

describe('Adversarial Stress Test: Rate Limiting & 429 Edge Cases (R2)', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    fetchSpy = vi.spyOn(global, 'fetch')
  })

  afterEach(() => {
    fetchSpy.mockRestore()
  })

  function mockRawResponse(status: number, rawText: string) {
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      headers: new Headers(),
      json: () => Promise.resolve(JSON.parse(rawText)),
      text: () => Promise.resolve(rawText),
    } as Response)
  }

  function mockResponse(status: number, body: any = {}) {
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      headers: new Headers(),
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    } as Response)
  }

  it('1. 429 with raw unlocalized detail string falls back to errors.RATE_LIMITED', async () => {
    fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: 'Too many requests' }))

    let error: SanitizedApiError | null = null
    try {
      await api('/test')
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error).toBeInstanceOf(SanitizedApiError)
    expect(error?.status).toBe(429)
    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
    expect(error?.code).toBeNull()
  })

  it('2. 429 with valid i18n key in detail translates properly', async () => {
    fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: 'errors.SESSION_EXPIRED' }))

    let error: SanitizedApiError | null = null
    try {
      await api('/test')
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error).toBeInstanceOf(SanitizedApiError)
    expect(error?.status).toBe(429)
    expect(error?.message).toBe(i18n.t('errors.SESSION_EXPIRED'))
  })

  it('3. 429 on /browser/suche without code assigns BROWSER_SUCHE_ZU_VIELE', async () => {
    fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: 'Too many requests' }))

    let error: SanitizedApiError | null = null
    try {
      await api('/browser/suche', { method: 'POST', body: '{}' })
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error).toBeInstanceOf(SanitizedApiError)
    expect(error?.status).toBe(429)
    expect(error?.code).toBe('BROWSER_SUCHE_ZU_VIELE')
    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
  })

  it('4. 429 on /browser/suche with custom code preserves custom code from detail.code', async () => {
    fetchSpy.mockReturnValueOnce(
      mockResponse(429, {
        detail: {
          code: 'CUSTOM_SEARCH_RATE_LIMIT',
          message: 'Too many search requests',
        },
      }),
    )

    let error: SanitizedApiError | null = null
    try {
      await api('/browser/suche', { method: 'POST', body: '{}' })
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error).toBeInstanceOf(SanitizedApiError)
    expect(error?.status).toBe(429)
    expect(error?.code).toBe('CUSTOM_SEARCH_RATE_LIMIT')
    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
  })

  it('5. 429 on /browser/suche with custom code preserves custom code from root parsed.code', async () => {
    fetchSpy.mockReturnValueOnce(
      mockResponse(429, {
        code: 'ROOT_SEARCH_RATE_LIMIT',
        detail: 'Too many search requests',
      }),
    )

    let error: SanitizedApiError | null = null
    try {
      await api('/browser/suche', { method: 'POST', body: '{}' })
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error).toBeInstanceOf(SanitizedApiError)
    expect(error?.status).toBe(429)
    expect(error?.code).toBe('ROOT_SEARCH_RATE_LIMIT')
  })

  it('6. 429 on /suche endpoint also sets BROWSER_SUCHE_ZU_VIELE if no code present', async () => {
    fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: 'Rate limit exceeded' }))

    let error: SanitizedApiError | null = null
    try {
      await api('/suche', { method: 'POST' })
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error?.code).toBe('BROWSER_SUCHE_ZU_VIELE')
    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
  })

  it('7. 429 with empty response text cleanly falls back to errors.RATE_LIMITED', async () => {
    fetchSpy.mockReturnValueOnce(mockRawResponse(429, ''))

    let error: SanitizedApiError | null = null
    try {
      await api('/test')
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error).toBeInstanceOf(SanitizedApiError)
    expect(error?.status).toBe(429)
    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
    expect(error?.code).toBeNull()
  })

  it('8. 429 with HTML/proxy response body cleanly falls back to errors.RATE_LIMITED', async () => {
    fetchSpy.mockReturnValueOnce(mockRawResponse(429, '<!DOCTYPE html><html><body><h1>429 Too Many Requests</h1></body></html>'))

    let error: SanitizedApiError | null = null
    try {
      await api('/test')
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error).toBeInstanceOf(SanitizedApiError)
    expect(error?.status).toBe(429)
    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
  })

  it('9. 429 with unknown translation key falls back to errors.RATE_LIMITED', async () => {
    fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: 'unknown.i18n.key.that.does.not.exist' }))

    let error: SanitizedApiError | null = null
    try {
      await api('/test')
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
  })

  it('10. 429 with German language active localizes correctly to German', async () => {
    await i18n.changeLanguage('de')
    fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: 'Too many requests' }))

    let error: SanitizedApiError | null = null
    try {
      await api('/test')
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
    expect(error?.message).toContain('Zu viele Anfragen')
  })

  it('11. 429 with null or non-object detail falls back to errors.RATE_LIMITED safely', async () => {
    fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: null }))

    let error: SanitizedApiError | null = null
    try {
      await api('/test')
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
  })

  it('12. 429 with numeric detail does not throw unexpected runtime exception', async () => {
    fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: 429 }))

    let error: SanitizedApiError | null = null
    try {
      await api('/test')
    } catch (e) {
      error = e as SanitizedApiError
    }

    expect(error?.message).toBe(i18n.t('errors.RATE_LIMITED'))
  })
})
