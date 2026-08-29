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
  monthly_realtime_cost_limit_cents: 2_500,
  max_memory_entries: 250,
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
  monthly_realtime_cost_limit_cents: null,
  max_memory_entries: null,
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
    fireEvent.click(await screen.findByRole('tab', { name: /Rollen & Kontingente/i }))
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
          // Die Reihenfolge ist hier bedeutsam: verglichen werden zwei
          // Zeichenketten, und der Rumpf entsteht in der Reihenfolge von
          // FIELD_DEFINITIONS. Wer ein Feld dort verschiebt, muss es auch hier
          // verschieben.
          max_memory_entries: 250,
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
    fireEvent.click(await screen.findByRole('tab', { name: /Rollen & Kontingente/i }))

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
    fireEvent.click(await screen.findByRole('tab', { name: /Rollen & Kontingente/i }))

    // Regression zum Quota-Blocker: eine unkonfigurierte Rolle darf hier nicht
    // wie ein gespeichertes Nulllimit aussehen. Wer das versehentlich
    // speichert, sperrt die KI fuer alle Traeger dieser Rolle.
    const unlimited = await screen.findByRole('switch', {
      name: /Unbegrenzt: Tägliches Tokenlimit: user/i,
    })
    expect(unlimited).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText(/noch kein Kontingent gespeichert/i)).toBeInTheDocument()
  })

  it('hängt den Hinweis an das Memory-Feld — und nur an dieses', async () => {
    render(<AiTab />)
    fireEvent.click(await screen.findByRole('tab', { name: /Rollen & Kontingente/i }))

    // Vier Unwahrheiten trug dieses Feld: die Beschriftung las sich wie ein
    // Vorrat je Benutzer (gezählt wird je Bereich, und wieviele es davon gibt,
    // bestimmt der Benutzer selbst), „Unbegrenzt" heißt hier seit dem Rückfall
    // auf die Systemgrenze nicht mehr unbegrenzt, im Teambereich entscheidet
    // die Rolle des Gründers, und das geteilte Serverwissen hängt an gar keiner
    // Rolle. Alles vier zieht allein der Hinweis gerade — deshalb muss er am
    // Feld hängen, nicht irgendwo.
    //
    // Gesucht wird über den Locale-Schlüssel statt über einen abgeschriebenen
    // Halbsatz, und geprüft wird nur die Verdrahtung. Hier stand vorher
    // `toHaveTextContent('Systemgrenze von 100 Einträgen')`: die deutsche
    // Locale gegen eine Kopie ihrer selbst — und im Widerspruch zum
    // Backend-Wächter `test_der_hinweis_nennt_genau_die_systemgrenze_aus_dem_code`,
    // der dieselbe Zeile an MAX_SYSTEM_SCOPE_ENTRIES bindet. Wer die Konstante
    // verschiebt und die Locales pflichtgemäß nachzieht — genau die Änderung,
    // für die jener Wächter gebaut wurde —, wurde hier rot, ohne etwas falsch
    // gemacht zu haben. Die Zahl gehört dem Backend, die Verdrahtung hierher.
    const feld = await screen.findByLabelText('Max. Memory-Einträge je Bereich: ai-vip')
    const hinweis = screen.getByText(i18n.t('aiSettings.maxMemoryEntriesHint'))
    expect(feld).toHaveAttribute('aria-describedby', hinweis.id)

    // Und wirklich nur dort. Die übrigen Felder sagen genau das, was ihre
    // Beschriftung sagt; ein Hinweis an jedem wäre Dekoration und würde den
    // einen, der etwas zu sagen hat, mit übersehen lassen.
    expect(screen.getAllByText(i18n.t('aiSettings.maxMemoryEntriesHint'))).toHaveLength(1)
    expect(screen.getByLabelText('Tägliches Tokenlimit: ai-vip')).not.toHaveAttribute('aria-describedby')
    expect(screen.getByLabelText('Monatliches Kostenlimit (Cent): ai-vip')).not.toHaveAttribute('aria-describedby')
    // Auch die Auswahl, nicht nur die Zahlenfelder: sie ist das einzige Feld
    // mit einem anderen Bauteil und würde einen Fehler dort sonst verstecken.
    expect(screen.getByLabelText('Höchste Denkstufe: ai-vip')).not.toHaveAttribute('aria-describedby')
  })

  it('sagt den Hinweis auch am Schalter an, weil das Feld daneben abgeschaltet sein kann', async () => {
    render(<AiTab />)
    fireEvent.click(await screen.findByRole('tab', { name: /Rollen & Kontingente/i }))

    const schalter = await screen.findByRole('switch', {
      name: 'Unbegrenzt: Max. Memory-Einträge je Bereich: ai-vip',
    })
    const hinweis = screen.getByText(i18n.t('aiSettings.maxMemoryEntriesHint'))
    expect(schalter).toHaveAttribute('aria-describedby', hinweis.id)

    // Der Grund, warum die Beschreibung nicht allein am Zahlenfeld hängen darf:
    // ist „Unbegrenzt" an, ist das Feld `disabled` und damit aus der
    // Tabreihenfolge. Vorgelesen bekäme man dann nur noch „Unbegrenzt: Max.
    // Memory-Einträge je Bereich: ai-vip, Umschalter, aktiviert" — und gerade in
    // diesem Zustand ist „Unbegrenzt" die Unwahrheit, weil das Backend auf die
    // Systemgrenze zurückfällt. Der Test schaltet deshalb wirklich um, statt
    // nur das Attribut im Ausgangszustand zu zählen.
    fireEvent.click(schalter)

    expect(schalter).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByLabelText('Max. Memory-Einträge je Bereich: ai-vip')).toBeDisabled()
    expect(schalter).toHaveAttribute('aria-describedby', hinweis.id)

    // Und wieder nur an diesem einen: an den übrigen Schaltern sagt
    // „Unbegrenzt" weiterhin die Wahrheit und braucht keine Fußnote.
    expect(screen.getByRole('switch', {
      name: 'Unbegrenzt: Tägliches Tokenlimit: ai-vip',
    })).not.toHaveAttribute('aria-describedby')
    expect(screen.getByRole('switch', {
      name: 'Unbegrenzt: Höchste Denkstufe: ai-vip',
    })).not.toHaveAttribute('aria-describedby')
  })

  it('nennt die Ausnahme des Memory-Feldes auch in den beiden allgemeinen Regeltexten', async () => {
    // Die Karte erklärt sich aus drei Texten, und zwei davon standen dem
    // dritten entgegen: `ruleHelp` über dem Raster und `notConfiguredHint`
    // daneben versprechen „unbegrenzt", solange keine Rolle etwas hinterlegt
    // hat — für die Memory-Einträge ist das seit dem Rückfall auf die
    // Systemgrenze das Gegenteil dessen, was eine Zeile tiefer im Feldhinweis
    // steht. Beides wird zuerst gelesen, und beide Leserichtungen kosten:
    // wer oben las, legte an der großzügigen Rolle „Unbegrenzt" um und nahm ihr
    // damit jeden Beitrag; wer das leere Feld für offen hielt, plante gegen eine
    // Zahl, die das Panel nie durchsetzt. Kein Test hat die beiden Schlüssel je
    // gelesen — der Wächter am Feldhinweis prüft nur `maxMemoryEntriesHint` und
    // wäre grün geblieben.
    vi.mocked(client.api).mockImplementation((pfad: string) => (
      pfad === '/ai/settings/role-limits' ? Promise.resolve([blankRow]) : respond(pfad)
    ) as never)
    render(<AiTab />)
    fireEvent.click(await screen.findByRole('tab', { name: /Rollen & Kontingente/i }))

    // Beide Sätze müssen wirklich auf dem Schirm stehen, nicht nur in der
    // Locale: `notConfiguredHint` erscheint ausschließlich an einer
    // unkonfigurierten Rolle — also genau dort, wo er in die Irre führte.
    await screen.findByText(i18n.t('aiSettings.ruleHelp'))
    screen.getByText(i18n.t('aiSettings.notConfiguredHint'))

    // Gesucht wird die Ausnahme über die Beschriftung des Feldes, das sie
    // betrifft, und die kommt aus derselben Locale statt aus einer Kopie hier.
    // Die Systemgrenze bleibt bewusst draußen: ihre Zahl gehört dem
    // Backend-Wächter `test_der_hinweis_nennt_genau_die_systemgrenze_aus_dem_code`,
    // der sie an MAX_SYSTEM_SCOPE_ENTRIES bindet — eine zweite Fassung hier
    // würde bei jeder pflichtgemäßen Änderung rot, ohne dass etwas falsch ist.
    // Beide Sprachen, weil die englische Fassung sonst still zurückfallen
    // könnte auf einen Satz, der die Ausnahme nicht kennt.
    for (const sprache of ['de', 'en'] as const) {
      const text = i18n.getFixedT(sprache)
      const feld = text('aiSettings.maxMemoryEntries')
      expect(text('aiSettings.ruleHelp')).toContain(feld)
      expect(text('aiSettings.notConfiguredHint')).toContain(feld)
    }
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
    fireEvent.click(await screen.findByRole('tab', { name: /Rollen & Kontingente/i }))
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
    fireEvent.click(await screen.findByRole('tab', { name: /Funktionen & Wissen/i }))

    expect(await screen.findByTestId('skills')).toHaveAttribute('data-scope', 'panel')
    expect(screen.getByTestId('memory')).toHaveAttribute('data-scope', 'panel')
  })

  it('zeigt panelweite Skills nicht ohne das Verwaltungsrecht', async () => {
    permissions.mockImplementation((key: string) => key !== 'ai.skills.manage')
    render(<AiTab />)
    fireEvent.click(await screen.findByRole('tab', { name: /Funktionen & Wissen/i }))

    await screen.findByTestId('memory')
    expect(screen.queryByTestId('skills')).not.toBeInTheDocument()
  })

  it('zeigt die KI-Nutzung aller Benutzer mit dem passenden Recht', async () => {
    render(<AiTab />)
    fireEvent.click(await screen.findByRole('tab', { name: /Verbrauch & Kosten/i }))

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

    expect(screen.queryByRole('tab', { name: /Verbrauch & Kosten/i })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('KI-Nutzung')).not.toBeInTheDocument()
    expect(client.api).not.toHaveBeenCalledWith('/ai/usage')
  })
})
