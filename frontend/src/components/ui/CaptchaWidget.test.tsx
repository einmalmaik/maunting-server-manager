import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { CaptchaWidget } from './CaptchaWidget'

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
})
