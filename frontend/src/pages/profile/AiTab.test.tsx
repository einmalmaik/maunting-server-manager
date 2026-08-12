import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiTab } from './AiTab'

/**
 * Der Mock antwortet **pfadabhängig**, nicht pauschal mit `[]`.
 *
 * Vorher tat er Letzteres, und jede neue Abfrage auf dieser Seite brach ihn:
 * eine Antwort, die für eine Liste gedacht war, landete in einer Komponente,
 * die ein Objekt erwartete. Der Fehler sah dann nach einem Bug in der neuen
 * Komponente aus, war aber einer im Mock.
 */
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    api: vi.fn((path: string) => {
      if (path === '/ai/usage/me') {
        return Promise.resolve({
          user_id: 1, username: 'tester',
          tokens_today: 120, tokens_week: 800, tokens_month: 2_400,
          cost_month_micro_usd: 2_500_000, requests_month: 12, last_request_at: null,
          cost_policy: {
            currency: 'EUR', usd_rate: '0.92',
            available_currencies: ['EUR', 'USD'], min_rate: '0.01', max_rate: '100',
          },
          limits: {
            daily_token_limit: 1_000, weekly_token_limit: null,
            monthly_token_limit: null, requests_per_minute: null,
            concurrent_operations: null, monthly_cost_limit_cents: null,
            role_ids: [],
          },
        })
      }
      return Promise.resolve([])
    }),
  }
})

vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn().mockResolvedValue(true) }))
vi.mock('@/stores/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

/**
 * Der KI-Tab im Profil ist der Ort des **persönlichen** Wissens — und nur der.
 *
 * Vorher stand die Skill-Verwaltung darunter, obwohl ein Skill nie „für dieses
 * Profil" gilt: er gehört einem Team oder dem ganzen Panel. Wer hier etwas
 * eintrug, schrieb je nach Auswahl unbemerkt für alle.
 */
describe('Profil → KI', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: {
        is_owner: false, role_id: null, role_name: null,
        // Beide Rechte gesetzt: die Abwesenheit der Skills soll an der Seite
        // liegen und nicht daran, dass jemand sie ohnehin nicht sähe.
        global_keys: ['ai.memory.use', 'ai.skills.use', 'ai.skills.manage'],
        server_keys: {},
      },
      isLoading: false, error: null,
    })
  })

  it('zeigt das persönliche Gedächtnis', async () => {
    render(<MemoryRouter><AiTab /></MemoryRouter>)
    expect(await screen.findByLabelText('Persönliches KI-Memory')).toBeInTheDocument()
  })

  it('zeigt das eigene Kontingent samt Grenze', async () => {
    // Ohne diese Karte war „Kontingent ausgeschöpft" für den Betroffenen von
    // einem Fehler nicht zu unterscheiden: die Zahlen lagen im Backend, aber
    // an keiner Stelle, die er aufrufen konnte.
    render(<MemoryRouter><AiTab /></MemoryRouter>)

    const karte = await screen.findByLabelText('Dein KI-Kontingent')
    expect(karte).toBeInTheDocument()
    // Verbrauch und Grenze stehen nebeneinander — einzeln sagt keins von
    // beiden etwas aus.
    expect(karte).toHaveTextContent('120')
    expect(karte).toHaveTextContent('1.000')
    // Wo nichts hinterlegt ist, steht das ausdrücklich statt einer leeren
    // Fortschrittsleiste, die nach „0 %" aussähe.
    expect(karte).toHaveTextContent('Keine Grenze hinterlegt')
  })

  it('zeigt keine Skill-Verwaltung mehr, sondern den Weg dorthin', async () => {
    render(<MemoryRouter><AiTab /></MemoryRouter>)
    await screen.findByLabelText('Persönliches KI-Memory')

    await waitFor(() => expect(screen.queryByLabelText('Skills')).not.toBeInTheDocument())
    expect(screen.queryByLabelText('Skills des Assistenten')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Teams/ })).toHaveAttribute('href', '/teams')
  })
})
