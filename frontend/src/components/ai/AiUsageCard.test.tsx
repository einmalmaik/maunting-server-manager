import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AiUsageCard } from './AiUsageCard'
import { aiApi, type AiUsageMine } from '@/api/ai'
import i18n from '@/i18n'

vi.mock('@/api/ai', () => ({
  aiApi: { getMyUsage: vi.fn() },
}))

/**
 * Eine Antwort von `/ai/usage/me`. Nur die Tagesgrenze wird variiert — Woche
 * und Monat bleiben absichtlich `null`, damit „unbegrenzt" und „gesperrt" in
 * derselben Karte nebeneinander stehen und nicht verwechselt werden können.
 */
function usage(tokensToday: number, dailyLimit: number | null): AiUsageMine {
  return {
    user_id: 1,
    username: 'tester',
    tokens_today: tokensToday,
    tokens_week: 0,
    tokens_month: 0,
    cost_month_micro_usd: 0,
    requests_month: 0,
    last_request_at: null,
    cost_policy: {
      currency: 'EUR',
      usd_rate: '0.92',
      available_currencies: ['EUR', 'USD'],
      min_rate: '0.01',
      max_rate: '100',
    },
    limits: {
      daily_token_limit: dailyLimit,
      weekly_token_limit: null,
      monthly_token_limit: null,
      requests_per_minute: null,
      concurrent_operations: null,
      monthly_cost_limit_cents: null,
      monthly_realtime_cost_limit_cents: null,
      role_ids: [],
    },
  }
}

/**
 * Diese Karte ist die einzige Erklärung, die ein abgewiesener Benutzer bekommt.
 * Sie darf deshalb nicht das Gegenteil dessen behaupten, was das Backend tut:
 * eine Grenze von 0 ist dort eine echte Sperre (`_ensure_within`), kein
 * fehlendes Limit.
 */
describe('AiUsageCard', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.getMyUsage).mockReset()
  })

  it('nennt eine Grenze von 0 eine Sperre und nicht „keine Grenze“', async () => {
    vi.mocked(aiApi.getMyUsage).mockResolvedValue(usage(0, 0))
    render(<AiUsageCard />)

    // Genau einmal gesperrt (heute) und zweimal wirklich unbegrenzt (Woche,
    // Monat). Vorher stand dreimal „Keine Grenze hinterlegt" da.
    await waitFor(() => expect(screen.getAllByText(/Gesperrt/)).toHaveLength(1))
    expect(screen.getAllByText('Keine Grenze hinterlegt')).toHaveLength(2)
  })

  it('zeigt die Sperre als vollen roten Balken', async () => {
    vi.mocked(aiApi.getMyUsage).mockResolvedValue(usage(0, 0))
    render(<AiUsageCard />)

    const bar = await screen.findByRole('progressbar', { name: 'Heute' })
    expect(bar).toHaveAttribute('aria-valuenow', '100')
    expect(bar.firstElementChild?.className).toContain('bg-status-error')
  })

  it('warnt farblich, bevor das Kontingent aufgebraucht ist', async () => {
    vi.mocked(aiApi.getMyUsage).mockResolvedValue(usage(950, 1_000))
    render(<AiUsageCard />)

    const bar = await screen.findByRole('progressbar', { name: 'Heute' })
    expect(bar.firstElementChild?.className).toContain('bg-status-error')
  })

  it('lässt einen ruhigen Verbrauch ruhig aussehen', async () => {
    vi.mocked(aiApi.getMyUsage).mockResolvedValue(usage(50, 1_000))
    render(<AiUsageCard />)

    const bar = await screen.findByRole('progressbar', { name: 'Heute' })
    // Die Ruhefarbe von `Singra/UI/ProgressBar` — dieselbe wie bei den
    // CPU-/RAM-Balken, seit die Karte den Balken nicht mehr selbst nachbaut.
    expect(bar.firstElementChild?.className).toContain('bg-secondary')
  })

  it('behält „Keine Grenze hinterlegt“ für eine wirklich fehlende Grenze', async () => {
    vi.mocked(aiApi.getMyUsage).mockResolvedValue(usage(120, null))
    render(<AiUsageCard />)

    // Die Gegenprobe zum ersten Test: ohne Grenze bleibt es beim alten Satz,
    // und es entsteht kein Balken, der einen Anteil an nichts behauptet.
    await waitFor(() => expect(screen.getAllByText('Keine Grenze hinterlegt')).toHaveLength(3))
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('nennt die Kosten in der Anzeigewährung und daneben in Dollar', async () => {
    // Hier stand eine nackte Zahl ohne Währung, gerechnet als `cents / 100`.
    // Gebucht wird in US-Cent; welche Währung daraus wird, entscheidet die
    // Politik neben den Zahlen — und der Dollarbetrag steht daneben, weil sich
    // nur gegen ihn die Rechnung des Anbieters prüfen lässt.
    vi.mocked(aiApi.getMyUsage).mockResolvedValue({
      ...usage(0, null),
      // 2,00 USD gebucht, Kurs 0,92 → 1,84 €.
      cost_month_micro_usd: 2_000_000,
      requests_month: 4,
    })
    render(<AiUsageCard />)

    const zeile = await screen.findByText(/1,84/)
    expect(zeile.textContent?.replace(/\s/g, ' ')).toContain('1,84 €')
    expect(zeile.textContent?.replace(/\s/g, ' ')).toContain('(2,00 $)')
  })
})
