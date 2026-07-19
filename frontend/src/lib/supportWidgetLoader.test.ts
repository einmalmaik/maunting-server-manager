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

  it('injects Crisp script when crisp provider is configured', async () => {
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
    expect(script?.textContent).toContain('test-crisp-id')
  })

  it('injects Tawk script when tawk provider is configured', async () => {
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
    expect(script?.textContent).toContain('embed.tawk.to/prop123/wid456')
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
