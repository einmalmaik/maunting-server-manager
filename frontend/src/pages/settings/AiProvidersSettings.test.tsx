import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiProviderAdmin } from '@/api/ai'
import i18n from '@/i18n'
import { AiProvidersSettings } from './AiProvidersSettings'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listProviderSettings: vi.fn(),
    listProviderKinds: vi.fn(),
    listCatalogModels: vi.fn(),
    createProvider: vi.fn(),
    updateProvider: vi.fn(),
    deleteProvider: vi.fn(),
  },
}))

const provider: AiProviderAdmin = {
  id: 4,
  name: 'Internal AI',
  provider_kind: 'openrouter',
  base_url: 'https://openrouter.ai/api/v1',
  default_model: 'anthropic/claude-opus-5',
  enabled: true,
  requires_api_key: true,
  operator_key_configured: true,
  operator_key_hint: '********1234',
  token_price_cents_per_million: null,
  updated_at: '2026-08-01T12:00:00Z',
}

describe('AiProvidersSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.listProviderSettings).mockReset().mockResolvedValue([provider])
    vi.mocked(aiApi.updateProvider).mockReset().mockResolvedValue(provider)
    vi.mocked(aiApi.listProviderKinds).mockReset().mockResolvedValue([{
      kind: 'openrouter',
      label: 'OpenRouter',
      base_url: 'https://openrouter.ai/api/v1',
      key_url: 'https://openrouter.ai/keys',
      key_prefix: 'sk-or-',
    }])
    vi.mocked(aiApi.listCatalogModels).mockReset().mockResolvedValue([{
      model_id: 'anthropic/claude-opus-5',
      name: 'Claude Opus 5',
      reasoning: true,
      efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
      default_effort: 'high',
      mandatory: false,
    }])
  })

  it('never receives an existing secret and clears a replacement after save', async () => {
    render(<AiProvidersSettings canWrite />)
    const keyInput = await screen.findByLabelText('Operator-API-Key')

    expect(keyInput).toHaveValue('')
    expect(screen.queryByDisplayValue('operator-secret-value')).not.toBeInTheDocument()
    fireEvent.change(keyInput, { target: { value: 'new-secret-value' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(4, expect.objectContaining({
      operator_api_key: 'new-secret-value',
    })))
    await waitFor(() => expect(keyInput).toHaveValue(''))
  })

  it('offers the models from the catalog instead of a free text field', async () => {
    render(<AiProvidersSettings canWrite />)

    // Ausgewaehlt statt getippt: ein Tippfehler fiel bisher erst beim
    // Testaufruf auf, und ueber die Denkstufen wusste MSM so oder so nichts.
    //
    // Das Feld startet als Textfeld und wird erst zur Auswahl, wenn der
    // Katalog da ist — ein `findBy` allein griffe die erste Fassung ab.
    // Unser `Dropdown` statt eines nativen `<select>`: ein Knopf, der eine
    // Listbox oeffnet. Am Knopf steht, was gewaehlt ist.
    await waitFor(() =>
      expect(screen.getByLabelText('Standardmodell')).toHaveTextContent('anthropic/claude-opus-5'))
    expect(screen.getByLabelText('Standardmodell').tagName).toBe('BUTTON')

    // Und die Denkstufen des gewaehlten Modells stehen daneben.
    expect(await screen.findByText('Maximal')).toBeInTheDocument()
  })

  it('keeps a text field when the catalog is unavailable', async () => {
    // Der Katalog ist ein fremder Dienst. Faellt er aus, muss der Betreiber
    // sein Modell weiterhin eintragen koennen — ein leeres Dropdown waere die
    // schlechtere Antwort.
    vi.mocked(aiApi.listCatalogModels).mockRejectedValue(new Error('offline'))
    render(<AiProvidersSettings canWrite />)

    // Erst belegen, dass es **versucht** wurde. Ohne diese Zusicherung ginge
    // der Test auch dann durch, wenn der Katalog gar nicht abgefragt wird —
    // ein Textfeld sieht in beiden Faellen gleich aus.
    await waitFor(() => expect(aiApi.listCatalogModels).toHaveBeenCalledWith('openrouter'))
    expect(screen.getByLabelText('Standardmodell').tagName).toBe('INPUT')
    // Und der Betreiber erfaehrt, warum er tippen muss — samt der Folge, dass
    // die Denkstufen dieses Modells damit unbekannt bleiben.
    expect(await screen.findByText(/Modellkatalog des Anbieters ist gerade nicht erreichbar/i))
      .toBeInTheDocument()
  })
})
