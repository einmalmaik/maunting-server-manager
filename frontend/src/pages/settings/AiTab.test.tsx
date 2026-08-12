import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { AiTab, type AiRoleLimits } from './AiTab'

// `SanitizedApiError` bleibt die echte Klasse: die Seite entscheidet mit
// `instanceof`, ob eine Meldung vorzeigbar ist. Eine Attrappe ohne die Klasse
// würde genau diese Unterscheidung wegmocken.
vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: vi.fn(),
}))
vi.mock('./AiProvidersSettings', () => ({ AiProvidersSettings: () => null }))

// Die beiden Wissenspanels haben eigene Tests und eigene Endpunkte. Hier zaehlt
// nur, **dass** sie eingehaengt sind und mit welchem Bereich — panelweit, nicht
// persoenlich. Ein echtes Rendern wuerde diesen Test an ihre Ladewege binden.
vi.mock('@/components/ai/AiSkillManager', () => ({
  AiSkillManager: ({ scope }: { scope: { kind: string } }) => (
    <div data-testid="skills" data-scope={scope.kind} />
  ),
}))
vi.mock('@/components/ai/AiMemoryManager', () => ({
  AiMemoryManager: ({ scope }: { scope: { kind: string } }) => (
    <div data-testid="memory" data-scope={scope.kind} />
  ),
}))

const permissions = vi.fn((_key: string): boolean => true)
vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => permissions(key),
}))

const row: AiRoleLimits = {
  role_id: 9,
  role_name: 'ai-vip',
  configured: true,
  daily_token_limit: 10_000,
  weekly_token_limit: 50_000,
  monthly_token_limit: 200_000,
  requests_per_minute: 20,
  concurrent_operations: 2,
  monthly_cost_limit_cents: 5_000,
  // Rang 4 = "hoch". Diese Rolle darf tief denken lassen, aber nicht maximal.
  max_reasoning_effort: 4,
  updated_at: '2026-08-01T12:00:00Z',
}

/** Eine zweite, noch unkonfigurierte Rolle — der Normalfall im frischen Panel. */
const blankRow: AiRoleLimits = {
  role_id: 3,
  role_name: 'user',
  configured: false,
  daily_token_limit: null,
  weekly_token_limit: null,
  monthly_token_limit: null,
  requests_per_minute: null,
  concurrent_operations: null,
  monthly_cost_limit_cents: null,
  max_reasoning_effort: null,
  updated_at: null,
}

/** Zwei Benutzer mit Verbrauch — die Antwort von `/ai/usage`. */
const usage = {
  entries: [
    {
      user_id: 9, username: 'viel-verbraucher', tokens_today: 1_200,
      tokens_week: 9_000, tokens_month: 40_000, cost_month_micro_usd: 3_500_000,
      requests_month: 88, last_request_at: '2026-08-10T09:00:00Z',
    },
    {
      user_id: 4, username: 'gelegentlich', tokens_today: 0,
      tokens_week: 40, tokens_month: 120, cost_month_micro_usd: 10_000,
      requests_month: 2, last_request_at: '2026-08-04T11:00:00Z',
    },
  ],
  total_tokens_month: 40_120,
  total_cost_month_micro_usd: 3_510_000,
  cost_policy: {
    currency: 'EUR', usd_rate: '0.92',
    available_currencies: ['EUR', 'USD'], min_rate: '0.01', max_rate: '100',
  },
}

/**
 * Antwortet pfadabhängig statt pauschal.
 *
 * Die Seite hängt inzwischen mehrere Endpunkte ein, und die liefern
 * unterschiedliche Formen. Ein Mock, der jedem davon dieselbe Liste gibt,
 * lässt eine Komponente an einer Antwort scheitern, die es so nie gibt.
 */
function respond(path: string): Promise<unknown> {
  if (path === '/ai/usage') return Promise.resolve(usage)
  // Die Einzelaufstellung und die Waehrungspolitik haengen an derselben Seite.
  // Ohne eigene Antwort bekaemen sie die Rollenliste und scheiterten daran.
  if (path.startsWith('/ai/usage/events')) {
    return Promise.resolve({ entries: [], has_more: false, cost_policy: usage.cost_policy })
  }
  if (path === '/ai/settings/cost') return Promise.resolve(usage.cost_policy)
  return Promise.resolve([row])
}

describe('AiTab', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    permissions.mockReturnValue(true)
    useToastStore.setState({ toasts: [] })
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation((path: string) => respond(path) as never)
  })

  it('loads role limits and saves a complete set including unlimited', async () => {
    render(<AiTab />)
    await screen.findByRole('switch', {
      name: /Unbegrenzt: Monatliches Tokenlimit: ai-vip/i,
    })

    fireEvent.click(screen.getByRole('switch', {
      name: /Unbegrenzt: Monatliches Tokenlimit: ai-vip/i,
    }))
    vi.mocked(client.api).mockResolvedValue({ ...row, monthly_token_limit: null })
    fireEvent.click(screen.getByRole('button', { name: /Speichern: ai-vip/i }))

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith('/ai/settings/role-limits/9', {
        method: 'PUT',
        body: JSON.stringify({
          daily_token_limit: 10_000,
          weekly_token_limit: 50_000,
          monthly_token_limit: null,
          requests_per_minute: 20,
          concurrent_operations: 2,
          monthly_cost_limit_cents: 5_000,
          // Der Rumpf entsteht aus FIELD_DEFINITIONS — ein neues Limit ist
          // damit automatisch mit im Speichern. Genau das prüft diese Zeile:
          // ein Feld hinzuzufügen, ohne den Speicherpfad anzufassen, darf nicht
          // dazu führen, dass der Wert im Formular steht und nie ankommt.
          max_reasoning_effort: 4,
        }),
      })
    })
  })

  it('shows only the selected role and switches to another one', async () => {
    vi.mocked(client.api).mockResolvedValue([blankRow, row])
    render(<AiTab />)

    // Vorauswahl faellt auf die bereits konfigurierte Rolle: dort gibt es
    // etwas zu sehen. Die unkonfigurierte Rolle ist gleichzeitig unsichtbar —
    // genau das war vorher das Problem, alle Rollen standen untereinander.
    await screen.findByRole('switch', { name: /Unbegrenzt: Monatliches Tokenlimit: ai-vip/i })
    expect(screen.queryByRole('switch', { name: /: user$/i })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Rolle' }))
    fireEvent.click(screen.getByRole('option', { name: /user/i }))

    await screen.findByRole('switch', { name: /Unbegrenzt: Monatliches Tokenlimit: user/i })
    expect(
      screen.queryByRole('switch', { name: /Unbegrenzt: Monatliches Tokenlimit: ai-vip/i }),
    ).not.toBeInTheDocument()
  })

  it('presents an unconfigured role as unlimited, never as a silent zero', async () => {
    vi.mocked(client.api).mockResolvedValue([blankRow])
    render(<AiTab />)

    // Regression zum Quota-Blocker: eine unkonfigurierte Rolle darf hier nicht
    // wie ein gespeichertes Nulllimit aussehen. Wer das versehentlich
    // speichert, sperrt die KI fuer alle Traeger dieser Rolle.
    const unlimited = await screen.findByRole('switch', {
      name: /Unbegrenzt: Tägliches Tokenlimit: user/i,
    })
    expect(unlimited).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText(/noch kein Kontingent gespeichert/i)).toBeInTheDocument()
  })

  it('shows API failures and does not silently discard them', async () => {
    // Eine Meldung aus einer verarbeiteten Backend-Antwort ist sanitisiert und
    // darf wörtlich angezeigt werden — nur die.
    vi.mocked(client.api).mockRejectedValue(new client.SanitizedApiError('AI-Limits nicht erreichbar'))
    render(<AiTab />)

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((toast) => toast.message === 'AI-Limits nicht erreichbar')).toBe(true)
    })
  })

  it('zeigt bei einem Netzwerkabbruch den übersetzten Satz statt der Browsermeldung', async () => {
    // Ist das Backend weg, wirft `fetch` einen blanken TypeError. Der ist keine
    // Backend-Antwort, und seine `message` ist die englische Meldung des
    // Browsers — unabhängig von der eingestellten Sprache.
    vi.mocked(client.api).mockRejectedValue(new TypeError('Failed to fetch'))
    render(<AiTab />)

    await waitFor(() => {
      const meldungen = useToastStore.getState().toasts.map((toast) => toast.message)
      expect(meldungen).toContain(i18n.t('aiSettings.loadFailed'))
      expect(meldungen).not.toContain('Failed to fetch')
    })
  })

  it('zeigt auch beim Speichern nicht die rohe Browsermeldung', async () => {
    render(<AiTab />)
    await screen.findByRole('switch', { name: /Unbegrenzt: Monatliches Tokenlimit: ai-vip/i })

    vi.mocked(client.api).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    fireEvent.click(screen.getByRole('button', { name: /Speichern: ai-vip/i }))

    await waitFor(() => {
      const meldungen = useToastStore.getState().toasts.map((toast) => toast.message)
      expect(meldungen).toContain(i18n.t('aiSettings.saveFailed'))
      expect(meldungen).not.toContain('Failed to fetch')
    })
  })

  it('does not request settings without read permission', () => {
    permissions.mockReturnValue(false)
    render(<AiTab />)

    expect(client.api).not.toHaveBeenCalled()
    expect(screen.getByText(/keine Berechtigung/i)).toBeInTheDocument()
  })

  it('zeigt panelweite Skills und panelweites Gedächtnis — beide panelweit', async () => {
    // Beides gilt für **jeden** Benutzer und gehört deshalb zum Betreiber.
    // Panelweites Gedächtnis lief bisher in jedem Gespräch mit, war aber nur
    // über die API erreichbar; panelweite Skills legte man im Profil an.
    render(<AiTab />)

    expect(await screen.findByTestId('skills')).toHaveAttribute('data-scope', 'panel')
    expect(screen.getByTestId('memory')).toHaveAttribute('data-scope', 'panel')
  })

  it('zeigt panelweite Skills nicht ohne das Verwaltungsrecht', async () => {
    permissions.mockImplementation((key: string) => key !== 'ai.skills.manage')
    render(<AiTab />)

    await screen.findByTestId('memory')
    expect(screen.queryByTestId('skills')).not.toBeInTheDocument()
  })

  it('zeigt die KI-Nutzung aller Benutzer mit dem passenden Recht', async () => {
    render(<AiTab />)

    const tabelle = await screen.findByLabelText('KI-Nutzung')
    expect(tabelle).toHaveTextContent('viel-verbraucher')
    expect(tabelle).toHaveTextContent('gelegentlich')
    // Die Summe steht als eigene Zeile: sie ist die Zahl, wegen der jemand
    // diese Seite überhaupt aufruft.
    expect(tabelle).toHaveTextContent('Gesamt')
    expect(tabelle).toHaveTextContent('3,51')
  })

  it('zeigt die KI-Nutzung nicht ohne ai.usage.read.all', async () => {
    // Der Kern dieses Rechts: es hängt **nicht** an panel.settings.read. Wer
    // Kontingente einstellen darf, sieht damit nicht automatisch, wer wieviel
    // verbraucht — das ist fremdes Nutzungsverhalten und eine eigene
    // Entscheidung des Betreibers.
    permissions.mockImplementation((key: string) => key !== 'ai.usage.read.all')
    render(<AiTab />)

    await screen.findByTestId('memory')
    expect(screen.queryByLabelText('KI-Nutzung')).not.toBeInTheDocument()
    expect(client.api).not.toHaveBeenCalledWith('/ai/usage')
  })
})
