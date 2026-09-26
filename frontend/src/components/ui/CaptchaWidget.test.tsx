import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { CaptchaWidget, type CaptchaStatus } from './CaptchaWidget'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

const TURNSTILE_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js'

function skript(): HTMLScriptElement | null {
  return document.querySelector(`script[src^="${TURNSTILE_SRC}"]`)
}

/**
 * Ein blockiertes Anbieterskript war bisher unsichtbar: leerer Kasten, Absenden
 * ohne Token, Ablehnung im Backend — und der einzige Hinweis stand in der
 * Browserkonsole. An der Stelle des Widgets muss ein Satz stehen.
 */
describe('CaptchaWidget', () => {
  beforeEach(async () => {
    Object.defineProperty(window, 'isSecureContext', {
      value: true,
      configurable: true,
      writable: true,
    })
    if (typeof (window as any).Worker === 'undefined') {
      ;(window as any).Worker = class {
        postMessage() {}
        terminate() {}
        addEventListener() {}
        removeEventListener() {}
      }
    }
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockResolvedValue({
      enabled: true,
      provider: 'turnstile',
      site_key: 'oeffentlicher-testschluessel',
    })
    await i18n.changeLanguage('de')
    skript()?.remove()
    delete (window as unknown as { turnstile?: unknown }).turnstile
  })

  afterEach(() => {
    skript()?.remove()
  })

  it('zeigt einen Hinweis, wenn das Anbieterskript gar nicht laedt', async () => {
    render(<CaptchaWidget onVerify={() => {}} />)

    await waitFor(() => expect(skript()).not.toBeNull())
    await act(async () => {
      skript()!.dispatchEvent(new Event('error'))
    })

    const hinweis = await screen.findByRole('alert')
    expect(hinweis.textContent).toContain('Sicherheitsabfrage konnte nicht geladen werden')
  })

  it('zeigt den Hinweis auch, wenn das Skript laedt, der Anbieter sich aber nie meldet', async () => {
    vi.useFakeTimers()
    try {
      render(<CaptchaWidget onVerify={() => {}} />)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })

      const geladenes = skript()
      expect(geladenes).not.toBeNull()
      await act(async () => {
        geladenes!.dispatchEvent(new Event('load'))
      })
      // 50 Versuche im 100-ms-Takt, bis der Widget-Global als verloren gilt.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(6000)
      })

      expect(screen.getByRole('alert').textContent).toContain(
        'Sicherheitsabfrage konnte nicht geladen werden',
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('meldet disabled, wenn keine Abfrage konfiguriert ist', async () => {
    vi.mocked(client.api).mockResolvedValue({ enabled: false, provider: 'none', site_key: '' })
    const status: CaptchaStatus[] = []
    render(<CaptchaWidget onVerify={() => {}} onStatusChange={(s) => status.push(s)} />)
    await waitFor(() => expect(status.at(-1)).toBe('disabled'))
    expect(status[0]).toBe('loading')
  })

  it('bleibt gesperrt, wenn die Konfiguration nicht ladbar ist', async () => {
    vi.mocked(client.api).mockRejectedValue(new Error('netz weg'))
    const status: CaptchaStatus[] = []
    render(<CaptchaWidget onVerify={() => {}} onStatusChange={(s) => status.push(s)} />)
    await waitFor(() => expect(status.at(-1)).toBe('failed'))
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('meldet failed, wenn das Anbieterskript geblockt wird', async () => {
    const status: CaptchaStatus[] = []
    render(<CaptchaWidget onVerify={() => {}} onStatusChange={(s) => status.push(s)} />)
    await waitFor(() => expect(skript()).not.toBeNull())
    await act(async () => {
      skript()!.dispatchEvent(new Event('error'))
    })
    expect(status.at(-1)).toBe('failed')
  })

  it('geht von ready ueber verified zurueck auf ready, wenn das Token ablaeuft', async () => {
    let optionen: Record<string, (token?: string) => void> = {}
    ;(window as unknown as { turnstile: unknown }).turnstile = {
      render: (_el: HTMLElement, opts: Record<string, (token?: string) => void>) => {
        optionen = opts
        return 'widget-1'
      },
      remove: () => {},
    }
    const status: CaptchaStatus[] = []
    const tokens: string[] = []
    render(<CaptchaWidget onVerify={(t) => tokens.push(t)} onStatusChange={(s) => status.push(s)} />)
    await waitFor(() => expect(status.at(-1)).toBe('ready'))

    await act(async () => optionen.callback('token-abc'))
    expect(status.at(-1)).toBe('verified')
    expect(tokens.at(-1)).toBe('token-abc')

    await act(async () => optionen['expired-callback']())
    expect(status.at(-1)).toBe('ready')
    expect(tokens.at(-1)).toBe('')
  })

  it('rendert das lokale ALTCHA-Widget und verarbeitet Verifikation', async () => {
    vi.mocked(client.api).mockResolvedValue({
      enabled: true,
      provider: 'altcha',
      site_key: '',
    })

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          algorithm: 'SHA-256',
          challenge: 'mock-challenge-123',
          maxnumber: 100,
          salt: `mock-salt?expires=${Math.floor(Date.now() / 1000) + 300}`,
          signature: 'mock-sig',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const onVerify = vi.fn()
    const status: CaptchaStatus[] = []
    const { container } = render(<CaptchaWidget onVerify={onVerify} onStatusChange={(s) => status.push(s)} />)

    await waitFor(() => {
      const widget = container.querySelector('altcha-widget')
      expect(widget).not.toBeNull()
      expect(widget?.getAttribute('challenge')).toContain('/api/auth/captcha-challenge')
    })

    const widget = container.querySelector('altcha-widget')!
    // Ohne Statusmeldung bliebe die Anmeldung mit ALTCHA dauerhaft gesperrt.
    expect(status.at(-1)).toBe('ready')

    // statechange event
    await act(async () => {
      widget.dispatchEvent(
        new CustomEvent('statechange', {
          detail: { state: 'verified', payload: 'mock-altcha-payload-123' },
        }),
      )
    })
    expect(onVerify).toHaveBeenCalledWith('mock-altcha-payload-123')
    expect(status.at(-1)).toBe('verified')

    // verified event
    await act(async () => {
      widget.dispatchEvent(
        new CustomEvent('verified', {
          detail: { payload: 'mock-altcha-payload-456' },
        }),
      )
    })
    expect(onVerify).toHaveBeenCalledWith('mock-altcha-payload-456')

    // expired resets token
    await act(async () => {
      widget.dispatchEvent(
        new CustomEvent('statechange', {
          detail: { state: 'expired' },
        }),
      )
    })
    expect(onVerify).toHaveBeenCalledWith('')
    expect(status.at(-1)).toBe('ready')

    // error resets token and shows alert
    await act(async () => {
      widget.dispatchEvent(
        new CustomEvent('statechange', {
          detail: { state: 'error' },
        }),
      )
    })
    expect(onVerify).toHaveBeenCalledWith('')
    expect(status.at(-1)).toBe('failed')
    const errorAlert = await screen.findByRole('alert')
    expect(errorAlert.textContent).toContain('Sicherheitsabfrage konnte nicht geladen werden')
  })
})

