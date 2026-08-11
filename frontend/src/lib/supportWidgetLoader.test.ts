import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { loadSupportWidget, notifySupportWidgetUpdated, SUPPORT_WIDGET_UPDATED_EVENT } from './supportWidgetLoader'
import { apiUrl } from '@/config/api'

describe('supportWidgetLoader', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, 'fetch')
    document.body.innerHTML = ''
  })

  afterEach(() => {
    fetchSpy.mockRestore()
    document.body.innerHTML = ''
  })

  function mockJsonResponse(data: any, status = 200) {
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(data),
    } as Response)
  }

  it('fetches support widget config using apiUrl()', async () => {
    fetchSpy.mockReturnValueOnce(mockJsonResponse({ enabled: false }))
    await loadSupportWidget()

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    expect(fetchSpy.mock.calls[0][0]).toBe(apiUrl('/system/support-widget'))
  })

  it('injects Singra widget script with data-widget-id when enabled', async () => {
    const testWidgetId = '4d677961-91fc-44d0-a990-e089d565d66c'
    fetchSpy.mockReturnValueOnce(
      mockJsonResponse({
        enabled: true,
        provider: 'singra',
        singra_widget_id: testWidgetId,
        script_src: 'https://singrabot.mauntingstudios.de/widget.js',
      })
    )

    await loadSupportWidget()

    const script = document.querySelector('script[data-msm-support-widget="singra"]') as HTMLScriptElement | null
    expect(script).not.toBeNull()
    expect(script?.getAttribute('data-widget-id')).toBe(testWidgetId)
    expect(script?.src).toBe('https://singrabot.mauntingstudios.de/widget.js')
    expect(script?.defer).toBe(true)
  })

  // Crisp und Tawk muessen ohne Inline-Code auskommen, sonst blockt die CSP des
  // Panels (script-src ohne 'unsafe-inline') das Startskript und das Widget
  // erscheint nie — ohne Fehlermeldung fuer den Betreiber.
  it('loads Crisp from an external src, never as inline script', async () => {
    fetchSpy.mockReturnValueOnce(
      mockJsonResponse({
        enabled: true,
        provider: 'crisp',
        crisp_website_id: 'test-crisp-id',
      })
    )

    await loadSupportWidget()

    const script = document.querySelector('script[data-msm-support-widget="crisp"]') as HTMLScriptElement | null
    expect(script).not.toBeNull()
    expect(script?.src).toBe('https://client.crisp.chat/l.js')
    expect(script?.textContent).toBe('')
    expect((window as any).CRISP_WEBSITE_ID).toBe('test-crisp-id')
    expect(Array.isArray((window as any).$crisp)).toBe(true)
  })

  it('loads Tawk from an external src, never as inline script', async () => {
    fetchSpy.mockReturnValueOnce(
      mockJsonResponse({
        enabled: true,
        provider: 'tawk',
        tawk_property_id: 'prop123',
        tawk_widget_id: 'wid456',
      })
    )

    await loadSupportWidget()

    const script = document.querySelector('script[data-msm-support-widget="tawk"]') as HTMLScriptElement | null
    expect(script).not.toBeNull()
    expect(script?.src).toBe('https://embed.tawk.to/prop123/wid456')
    expect(script?.textContent).toBe('')
    expect((window as any).Tawk_API).toBeTruthy()
  })

  // Das eigene Snippet stammt aus einem Einstellungsfeld und wird auch auf der
  // Loginseite an nicht angemeldete Besucher ausgeliefert. Es darf deshalb
  // weder Markup rendern noch Skripte ausfuehren.
  it('never renders custom snippet markup into the page', async () => {
    fetchSpy.mockReturnValueOnce(
      mockJsonResponse({
        enabled: true,
        provider: 'custom',
        custom_snippet:
          '<div id="fake-login" style="position:fixed;inset:0">Bitte Passwort erneut eingeben</div>',
      })
    )

    await loadSupportWidget()

    expect(document.getElementById('fake-login')).toBeNull()
    expect(document.getElementById('msm-support-widget-custom')).toBeNull()
    expect(document.body.textContent).not.toContain('Bitte Passwort erneut eingeben')
  })

  it('never revives an inline script from the custom snippet', async () => {
    fetchSpy.mockReturnValueOnce(
      mockJsonResponse({
        enabled: true,
        provider: 'custom',
        custom_snippet: '<script>window.__msmPwned = true</script>',
      })
    )

    await loadSupportWidget()

    expect(document.querySelector('script[data-msm-support-widget="custom"]')).toBeNull()
    expect((window as any).__msmPwned).toBeUndefined()
  })

  it('drops custom snippet scripts from origins the panel CSP does not allow', async () => {
    fetchSpy.mockReturnValueOnce(
      mockJsonResponse({
        enabled: true,
        provider: 'custom',
        custom_snippet: '<script src="https://evil.example/steal.js"></script>',
      })
    )

    await loadSupportWidget()

    expect(document.querySelector('script[src="https://evil.example/steal.js"]')).toBeNull()
    expect(document.querySelector('script[data-msm-support-widget="custom"]')).toBeNull()
  })

  it('keeps custom snippet scripts from an allowed origin', async () => {
    fetchSpy.mockReturnValueOnce(
      mockJsonResponse({
        enabled: true,
        provider: 'custom',
        custom_snippet: '<script src="https://client.crisp.chat/l.js" async></script>',
      })
    )

    await loadSupportWidget()

    const script = document.querySelector('script[data-msm-support-widget="custom"]') as HTMLScriptElement | null
    expect(script).not.toBeNull()
    expect(script?.src).toBe('https://client.crisp.chat/l.js')
  })

  it('removes widget artifacts when widget is disabled', async () => {
    // First inject a script
    fetchSpy.mockReturnValueOnce(
      mockJsonResponse({
        enabled: true,
        provider: 'singra',
        singra_widget_id: 'test-id',
        script_src: 'https://singrabot.mauntingstudios.de/widget.js',
      })
    )
    await loadSupportWidget()
    expect(document.querySelector('script[data-msm-support-widget]')).not.toBeNull()

    // Next load returns disabled
    fetchSpy.mockReturnValueOnce(mockJsonResponse({ enabled: false }))
    await loadSupportWidget()
    expect(document.querySelector('script[data-msm-support-widget]')).toBeNull()
  })

  it('dispatches custom update event via notifySupportWidgetUpdated', () => {
    const handler = vi.fn()
    window.addEventListener(SUPPORT_WIDGET_UPDATED_EVENT, handler)
    notifySupportWidgetUpdated()
    expect(handler).toHaveBeenCalledTimes(1)
    window.removeEventListener(SUPPORT_WIDGET_UPDATED_EVENT, handler)
  })
})
