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
    getCostPolicy: vi.fn(),
  },
}))

const provider: AiProviderAdmin = {
  id: 4,
  name: 'Internal AI',
  provider_kind: 'openrouter',
  base_url: 'https://openrouter.ai/api/v1',
  default_model: 'anthropic/claude-opus-5',
  default_voice: null,
  transcription_model: null,
  enabled: true,
  requires_api_key: true,
  operator_key_configured: true,
  operator_key_hint: '********1234',
  token_price_micro_usd_per_million: null,
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
      protokoll: 'chat_completions',
      katalog_braucht_schluessel: false,
    }, {
      // Der zweite Anbieter steht hier, damit die Auswahl im Test dieselbe
      // Entscheidung zu treffen hat wie im Betrieb: zwei Zugänge, die
      // verschiedene Dinge tun.
      kind: 'elevenlabs',
      label: 'ElevenLabs (Stimme)',
      base_url: 'https://api.elevenlabs.io/v1',
      key_url: 'https://elevenlabs.io/app/settings/api-keys',
      key_prefix: null,
      protokoll: 'tts',
      katalog_braucht_schluessel: true,
    }])
    vi.mocked(aiApi.getCostPolicy).mockReset().mockResolvedValue({
      currency: 'EUR',
      usd_rate: '0.92',
      available_currencies: ['EUR', 'USD'],
      min_rate: '0.01',
      max_rate: '100',
    })
    vi.mocked(aiApi.listCatalogModels).mockReset().mockResolvedValue([{
      model_id: 'anthropic/claude-opus-5',
      name: 'Claude Opus 5',
      reasoning: true,
      efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
      default_effort: 'high',
      mandatory: false,
      recommended: false,
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

  it('lässt einen Provider nach dem Löschen des Keys wieder einen bekommen', async () => {
    // `update()` merged in die vorhandene Zeile, und `toDraft` nannte
    // `clear_operator_api_key` nicht — die einmal gefasste Absicht „Key
    // entfernen" überlebte damit das Speichern. Danach war das Schlüsselfeld
    // dauerhaft gesperrt, und der Umschalter zum Zurücknehmen verschwand, weil
    // er nur bei `operator_key_configured` erscheint — das der Server gerade
    // auf `false` gesetzt hatte. Ohne Neuladen der Seite ging gar nichts mehr.
    vi.mocked(aiApi.updateProvider).mockResolvedValue({
      ...provider, operator_key_configured: false, operator_key_hint: null,
    })
    render(<AiProvidersSettings canWrite />)

    fireEvent.click(await screen.findByLabelText('Zentralen Operator-Key beim Speichern entfernen'))
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(4, expect.objectContaining({
      clear_operator_api_key: true,
    })))

    // Nach dem Speichern ist die Absicht verbraucht: das Feld nimmt wieder
    // einen Schlüssel an.
    const keyInput = await screen.findByLabelText('Operator-API-Key')
    await waitFor(() => expect(keyInput).not.toBeDisabled())
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
    //
    // Die Kennung des Zugangs geht mit, weil manche Anbieter ihren Katalog nur
    // gegen den Schluessel herausgeben. Fuer OpenRouter aendert das nichts —
    // der Aufruf traegt sie trotzdem, und genau das haelt dieser Test fest.
    await waitFor(() => expect(aiApi.listCatalogModels)
      .toHaveBeenCalledWith('openrouter', false, provider.id))
    expect(screen.getByLabelText('Standardmodell').tagName).toBe('INPUT')
    // Und der Betreiber erfaehrt, warum er tippen muss — samt der Folge, dass
    // die Denkstufen dieses Modells damit unbekannt bleiben.
    expect(await screen.findByText(/Modellkatalog des Anbieters ist gerade nicht erreichbar/i))
      .toBeInTheDocument()
  })

  it('nimmt „1,20" als Preis an und zeigt, was daraus in Dollar wird', async () => {
    // Der eigentliche Anlass: das Feld war ein Zaehler in ganzen Cent, und
    // zwischen 1 und 2 lag nichts. Ein Preis ist eine Dezimalzahl.
    render(<AiProvidersSettings canWrite />)
    const preisFeld = await screen.findByLabelText(/Rückfallpreis je 1 Mio\. Tokens \(EUR\)/)

    fireEvent.change(preisFeld, { target: { value: '1,20' } })
    fireEvent.blur(preisFeld)

    // 1,20 EUR bei Kurs 0,92 sind 1,304348 USD — aufgerundet auf die Microunit,
    // wie ueberall bei Kosten. Und der Betreiber sieht es, statt dass die
    // Umrechnung im Verborgenen passiert.
    await waitFor(() => expect(screen.getByText(/1,3043/)).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))
    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(
      4,
      expect.objectContaining({ token_price_micro_usd_per_million: 1_304_348 }),
    ))
  })

  it('offers the recommended model and takes it over on one click', async () => {
    // Die Empfehlung kommt aus dem Katalog, nicht aus der Oberflaeche. Genau
    // deshalb ist sie hier ein Feld an einem Modell und keine Zeichenkette im
    // Test: fuehrt der Anbieter die Kennung nicht mehr, verschwindet sie.
    vi.mocked(aiApi.listCatalogModels).mockResolvedValue([
      {
        model_id: 'anthropic/claude-opus-5',
        name: 'Claude Opus 5',
        reasoning: true,
        efforts: ['low', 'high'],
        default_effort: 'high',
        mandatory: false,
        recommended: false,
      },
      {
        model_id: 'openai/gpt-5.6-luna',
        name: 'GPT-5.6 Luna',
        reasoning: true,
        efforts: ['low', 'medium', 'high'],
        default_effort: 'medium',
        mandatory: false,
        recommended: true,
      },
    ])

    render(<AiProvidersSettings canWrite />)

    const uebernehmen = await screen.findByRole('button', { name: 'Übernehmen' })
    expect(screen.getByText(/MSM ist mit openai\/gpt-5\.6-luna erprobt/)).toBeInTheDocument()

    fireEvent.click(uebernehmen)

    // Uebernommen — und der Hinweis geht weg. Er soll nicht ueber eine
    // Entscheidung belehren, die schon gefallen ist.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Übernehmen' })).not.toBeInTheDocument(),
    )
  })

  it('says nothing when the provider no longer lists the recommended model', async () => {
    // Kein Sonderfall, sondern der Normalfall von morgen: Modelle werden
    // umbenannt und abgekuendigt. Dann zeigt MSM keine Empfehlung — nie eine
    // auf ein Modell, das es beim Anbieter nicht gibt.
    render(<AiProvidersSettings canWrite />)

    await screen.findByLabelText('Operator-API-Key')
    expect(screen.queryByRole('button', { name: 'Übernehmen' })).not.toBeInTheDocument()
  })
})
