import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api } from './client'

describe('api client', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    fetchSpy = vi.spyOn(global, 'fetch')
    // Clear cookies (must include secure flag for __Secure- prefixed cookies)
    document.cookie.split(';').forEach((c) => {
      const [name] = c.split('=')
      document.cookie = `${name.trim()}=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;secure`
    })
  })

  afterEach(() => {
    fetchSpy.mockRestore()
  })

  function mockResponse(status: number, body: any = {}, headers?: Record<string, string>) {
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      headers: new Headers(headers || {}),
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    } as Response)
  }

  describe('CSRF header', () => {
    it('should send X-CSRF-Token for POST requests', async () => {
      document.cookie = '__Secure-csrf_token=test_csrf_value;path=/;secure'
      fetchSpy.mockReturnValueOnce(mockResponse(200, { ok: true }))

      await api('/test', { method: 'POST', body: '{}' })

      const call = fetchSpy.mock.calls[0]
      const options = call[1] as RequestInit
      expect(options.headers).toMatchObject({
        'Content-Type': 'application/json',
        'X-CSRF-Token': 'test_csrf_value',
      })
    })

    it('should send X-CSRF-Token for PUT requests', async () => {
      document.cookie = '__Secure-csrf_token=put_csrf;path=/;secure'
      fetchSpy.mockReturnValueOnce(mockResponse(200, { ok: true }))

      await api('/test', { method: 'PUT' })

      const options = fetchSpy.mock.calls[0][1] as RequestInit
      expect((options.headers as Record<string, string>)['X-CSRF-Token']).toBe('put_csrf')
    })

    it('should send X-CSRF-Token for PATCH requests', async () => {
      document.cookie = '__Secure-csrf_token=patch_csrf;path=/;secure'
      fetchSpy.mockReturnValueOnce(mockResponse(200, { ok: true }))

      await api('/test', { method: 'PATCH' })

      const options = fetchSpy.mock.calls[0][1] as RequestInit
      expect((options.headers as Record<string, string>)['X-CSRF-Token']).toBe('patch_csrf')
    })

    it('should send X-CSRF-Token for DELETE requests', async () => {
      document.cookie = '__Secure-csrf_token=del_csrf;path=/;secure'
      fetchSpy.mockReturnValueOnce(mockResponse(200, { ok: true }))

      await api('/test', { method: 'DELETE' })

      const options = fetchSpy.mock.calls[0][1] as RequestInit
      expect((options.headers as Record<string, string>)['X-CSRF-Token']).toBe('del_csrf')
    })

    it('should NOT send X-CSRF-Token for GET requests', async () => {
      document.cookie = '__Secure-csrf_token=get_csrf;path=/;secure'
      fetchSpy.mockReturnValueOnce(mockResponse(200, { ok: true }))

      await api('/test')

      const options = fetchSpy.mock.calls[0][1] as RequestInit
      expect((options.headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined()
    })

    it('should NOT send X-CSRF-Token when cookie is missing', async () => {
      fetchSpy.mockReturnValueOnce(mockResponse(200, { ok: true }))

      await api('/test', { method: 'POST' })

      const options = fetchSpy.mock.calls[0][1] as RequestInit
      expect((options.headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined()
    })
  })

  describe('credentials', () => {
    it('should always include credentials: include', async () => {
      fetchSpy.mockReturnValueOnce(mockResponse(200, { ok: true }))

      await api('/test')

      const options = fetchSpy.mock.calls[0][1] as RequestInit
      expect(options.credentials).toBe('include')
    })
  })

  describe('token refresh on 401', () => {
    it('should call /auth/refresh on 401 and retry', async () => {
      // First call: 401, Refresh: success, Retry: success
      fetchSpy
        .mockReturnValueOnce(mockResponse(401, { detail: 'Unauthorized' }))
        .mockReturnValueOnce(mockResponse(200, { message: 'refreshed' }))
        .mockReturnValueOnce(mockResponse(200, { ok: true }))

      document.cookie = '__Secure-csrf_token=initial;path=/'

      await api('/test')

      expect(fetchSpy).toHaveBeenCalledTimes(3)
      // First call
      expect(fetchSpy.mock.calls[0][0]).toBe('/api/test')
      // Refresh call
      expect(fetchSpy.mock.calls[1][0]).toBe('/api/auth/refresh')
      expect((fetchSpy.mock.calls[1][1] as RequestInit).method).toBe('POST')
      // Retry call
      expect(fetchSpy.mock.calls[2][0]).toBe('/api/test')
    })

    it('should throw SESSION_EXPIRED when refresh fails', async () => {
      fetchSpy
        .mockReturnValueOnce(mockResponse(401, { detail: 'Unauthorized' }))
        .mockReturnValueOnce(mockResponse(401, { detail: 'Invalid refresh' }))

      await expect(api('/test')).rejects.toThrow('SESSION_EXPIRED')
    })

    it('should NOT refresh on /auth/login 401', async () => {
      fetchSpy.mockReturnValueOnce(mockResponse(401, { detail: 'Bad credentials' }))

      await expect(api('/auth/login', { method: 'POST' })).rejects.toThrow('Bad credentials')
      expect(fetchSpy).toHaveBeenCalledTimes(1)
    })

    it('should NOT refresh on /auth/refresh 401', async () => {
      fetchSpy.mockReturnValueOnce(mockResponse(401, { detail: 'Invalid refresh' }))

      await expect(api('/auth/refresh', { method: 'POST' })).rejects.toThrow('Invalid refresh')
      expect(fetchSpy).toHaveBeenCalledTimes(1)
    })
  })

  describe('rate limiting', () => {
    it('should throw RATE_LIMITED on 429', async () => {
      fetchSpy.mockReturnValueOnce(mockResponse(429, { detail: 'Too many requests' }))

      await expect(api('/test')).rejects.toThrow('RATE_LIMITED')
    })
  })

  describe('error handling', () => {
    it('should throw with detail from error response', async () => {
      fetchSpy.mockReturnValueOnce(mockResponse(500, { detail: 'Server error' }))

      await expect(api('/test')).rejects.toThrow('Server error')
    })

    it('should throw generic error when body has no detail', async () => {
      fetchSpy.mockReturnValueOnce({
        ok: false,
        status: 502,
        json: () => Promise.reject(new Error('bad json')),
        text: () => Promise.resolve('bad gateway'),
      } as Response)

      // client.ts falls back to { detail: 'Unbekannter Fehler' } when json fails
      await expect(api('/test')).rejects.toThrow('Unbekannter Fehler')
    })
  })
})
