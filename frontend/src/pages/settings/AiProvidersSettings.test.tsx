import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiProviderAdmin, type AiSprachweg } from '@/api/ai'
import i18n from '@/i18n'
import { AiProvidersSettings } from './AiProvidersSettings'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listProviderSettings: vi.fn(),
    listProviderKinds: vi.fn(),
    listCatalogModels: vi.fn(),
    findCatalogModel: vi.fn(),
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
  worker_model: null,
  worker_reasoning_effort: null,
  ethics_model: null,
  ethics_reasoning_effort: null,
  ethics_mode: 'off',
  azure_resource_name: null,
  enabled: true,
  requires_api_key: true,
  operator_key_configured: true,
  operator_key_hint: '********1234',
  token_price_micro_usd_per_million: null,
  updated_at: '2026-08-01T12:00:00Z',
}

/**
 * Die beiden Sprachwege eines OpenAI-Zugangs, wie `/settings/provider-kinds`
 * sie schickt (`Sprachweg.als_dict` in backend/services/ai_voice/sprachwege.py).
 * Abgeschrieben, nicht erfunden: jede Abweichung hier wäre eine Oberfläche,
 * die anders urteilt als das Backend.
 */
const OPENAI_SPRACHWEGE: AiSprachweg[] = [{
  weg: 'openai_realtime',
  merkmal: 'realtime',
  ausschluesse: [],
  stimmen: ['marin', 'cedar', 'alloy', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse'],
  empfohlene_stimmen: ['marin', 'cedar'],
  empfohlene_modelle: ['gpt-realtime-1.5', 'gpt-realtime-2'],
  denkstufen: ['low', 'medium', 'high'],
  denkstufen_merkmal: 'realtime-2',
  denkt_im_backend: false,
  vad: true,
  audiopreise: true,
  minutenpreis: false,
  backend_modell: false,
}, {
  weg: 'openai_live',
  merkmal: 'gpt-live',
  ausschluesse: ['transcribe', 'translate'],
  stimmen: [
    'marin', 'alloy', 'ash', 'ballad', 'beacon', 'bossa', 'cedar', 'cinder',
    'coral', 'delta', 'echo', 'gleam', 'meridian', 'quartz', 'ripple', 'sage',
    'shimmer', 'stone', 'tempo', 'verse', 'vesper', 'willow',
  ],
  empfohlene_stimmen: ['marin'],
  empfohlene_modelle: ['gpt-live-1'],
  denkstufen: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'],
  denkstufen_merkmal: null,
  denkt_im_backend: true,
  vad: false,
  audiopreise: false,
  minutenpreis: true,
  backend_modell: true,
}]

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
      ressource_noetig: false,
      fuehrt_katalog: true,
      kann_hoeren: true,
    }, {
      kind: 'openai',
      label: 'OpenAI',
      base_url: 'https://api.openai.com/v1',
      key_url: 'https://platform.openai.com/api-keys',
      key_prefix: 'sk-',
      protokoll: 'chat_completions',
      katalog_braucht_schluessel: true,
      ressource_noetig: false,
      fuehrt_katalog: true,
      kann_hoeren: true,
      realtime_tauglich: true,
      sprachwege: OPENAI_SPRACHWEGE,
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
      ressource_noetig: false,
      fuehrt_katalog: true,
      kann_hoeren: false,
    }, {
      // Und der dritte: der einzige, der eine Adresse braucht und keine
      // Modelliste führt. Beides zusammen gibt es nur bei Azure.
      kind: 'azure_openai',
      label: 'Azure OpenAI',
      base_url: 'https://{ressource}.services.ai.azure.com/openai/v1',
      key_url: 'https://ai.azure.com/',
      key_prefix: null,
      protokoll: 'chat_completions',
      katalog_braucht_schluessel: false,
      ressource_noetig: true,
      fuehrt_katalog: false,
      kann_hoeren: false,
    }])
    vi.mocked(aiApi.findCatalogModel).mockReset().mockResolvedValue(null)
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
      vision: null,
    }])
  })

  it('never receives an existing secret and clears a replacement after save', async () => {
    render(<AiProvidersSettings canWrite />)
    const keyInput = await screen.findByLabelText('API-Key')

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

    fireEvent.click(await screen.findByLabelText('Key entfernen'))
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(4, expect.objectContaining({
      clear_operator_api_key: true,
    })))

    // Nach dem Speichern ist die Absicht verbraucht: das Feld nimmt wieder
    // einen Schlüssel an.
    const keyInput = await screen.findByLabelText('API-Key')
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
      expect(screen.getByLabelText(/Standardmodell/i)).toHaveTextContent('anthropic/claude-opus-5'))
    expect(screen.getByLabelText(/Standardmodell/i).tagName).toBe('BUTTON')

    // Und die Denkstufen des gewaehlten Modells stehen daneben.
    expect(await screen.findByText('Maximal')).toBeInTheDocument()
  })

  it('sagt „unbekannt" statt „denkt nicht" und zeigt Fenster und Abschalttag', async () => {
    // Am 22.09.2026 stand GPT-6 Luna mit „Dieses Modell denkt nicht nach" in
    // der Auswahl: der Katalog wusste es noch nicht, und aus seinem Schweigen
    // wurde ein Nein. `null` ist eine eigene Auskunft.
    vi.mocked(aiApi.listCatalogModels).mockResolvedValue([{
      model_id: 'anthropic/claude-opus-5',
      name: 'Claude Opus 5',
      reasoning: null,
      efforts: [],
      default_effort: null,
      mandatory: false,
      recommended: false,
      vision: null,
      context_tokens: 1_050_000,
      max_output_tokens: 128_000,
      shutdown_date: '2026-10-23',
    }])
    render(<AiProvidersSettings canWrite />)

    expect(await screen.findByText(/Der Katalog sagt nicht, ob dieses Modell nachdenkt/))
      .toBeInTheDocument()
    expect(screen.queryByText('Dieses Modell denkt nicht nach.')).not.toBeInTheDocument()
    expect(screen.getByText('Kontextfenster: 1.050.000 Token · Antwort bis 128.000 Token'))
      .toBeInTheDocument()
    // Ein Kalendertag, in UTC gelesen — sonst stünde westlich von Greenwich
    // der 22. Oktober da.
    expect(screen.getByText(/Abschaltung dieses Modells für den 23\. Oktober 2026/))
      .toBeInTheDocument()
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
    expect(screen.getByLabelText(/Standardmodell/i).tagName).toBe('INPUT')
    // Und der Betreiber erfaehrt, warum er tippen muss — samt der Folge, dass
    // die Denkstufen dieses Modells damit unbekannt bleiben.
    expect(await screen.findByText(/Modellkatalog des Anbieters ist gerade nicht erreichbar/i))
      .toBeInTheDocument()
  })

  it('nimmt „1,20" als Preis an und speichert ihn in Dollar', async () => {
    // Der eigentliche Anlass: das Feld war ein Zaehler in ganzen Cent, und
    // zwischen 1 und 2 lag nichts. Ein Preis ist eine Dezimalzahl.
    render(<AiProvidersSettings canWrite />)
    const preisFeld = await screen.findByLabelText(/Eingabe · EUR \/ 1 Mio\. Tokens/)

    fireEvent.change(preisFeld, { target: { value: '1,20' } })
    fireEvent.blur(preisFeld)

    // 1,20 EUR bei Kurs 0,92 sind 1,304348 USD — aufgerundet auf die Microunit,
    // wie ueberall bei Kosten.
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(
      4,
      expect.objectContaining({ standard_input_price_micro_usd_per_million: 1_304_348 }),
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
        vision: null,
      },
      {
        model_id: 'openai/gpt-5.6-luna',
        name: 'GPT-5.6 Luna',
        reasoning: true,
        efforts: ['low', 'medium', 'high'],
        default_effort: 'medium',
        mandatory: false,
        recommended: true,
        vision: null,
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

    await screen.findByLabelText('API-Key')
    expect(screen.queryByRole('button', { name: 'Übernehmen' })).not.toBeInTheDocument()
  })

  it('zeigt einem TTS-Zugang weiterhin die Sprachmodell-Auswahl', async () => {
    // Die Regression vom 17.08.: der Umbau in Sub-Tabs hatte die Modellwahl
    // hinter das chat_completions-Gate geschoben — ElevenLabs verlor damit
    // das Feld, obwohl der TTS-Adapter `default_model` weiterhin liest.
    vi.mocked(aiApi.listProviderSettings).mockResolvedValue([{
      ...provider,
      id: 7,
      name: 'Stimme',
      provider_kind: 'elevenlabs',
      base_url: 'https://api.elevenlabs.io/v1',
      default_model: 'eleven_flash_v2_5',
      default_voice: '21m00Tcm4TlvDq8ikWAM',
    }])
    vi.mocked(aiApi.listCatalogModels).mockResolvedValue([{
      model_id: 'eleven_flash_v2_5',
      name: 'Eleven Flash v2.5',
      reasoning: false,
      efforts: [],
      default_effort: null,
      mandatory: false,
      recommended: true,
      vision: null,
    }, {
      model_id: 'eleven_multilingual_v2',
      name: 'Eleven Multilingual v2',
      reasoning: false,
      efforts: [],
      default_effort: null,
      mandatory: false,
      recommended: false,
      vision: null,
    }])
    render(<AiProvidersSettings canWrite />)

    // Auswahl statt Textfeld, sobald der Katalog da ist — und am Knopf steht,
    // was gewaehlt ist.
    await waitFor(() =>
      expect(screen.getByLabelText('Sprachmodell')).toHaveTextContent('eleven_flash_v2_5'))
    expect(screen.getByLabelText('Sprachmodell').tagName).toBe('BUTTON')
    // Die Stimme bleibt daneben bestehen.
    expect(screen.getByLabelText('Stimme')).toHaveValue('21m00Tcm4TlvDq8ikWAM')
  })

  it('verlangt bei Azure den Ressourcennamen und fragt keinen Katalog ab', async () => {
    // Der einzige Anbieter, bei dem der Betreiber ein Stueck der Adresse
    // beitraegt — und der einzige ohne Modelliste. Beides haengt am Anbieter
    // und nicht an seinem Namen: das Formular liest `ressource_noetig` und
    // `fuehrt_katalog`, nie `provider_kind === 'azure_openai'`.
    vi.mocked(aiApi.listProviderSettings).mockResolvedValue([{
      ...provider,
      id: 7,
      name: 'Azure',
      provider_kind: 'azure_openai',
      base_url: null,
      default_model: 'gpt-5.1',
      azure_resource_name: null,
    }])
    render(<AiProvidersSettings canWrite />)

    const feld = await screen.findByLabelText('Azure-Ressourcenname')
    expect(feld).toHaveValue('')
    // Ohne Namen hat der Zugang keine Adresse — Speichern bleibt gesperrt.
    expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled()

    // Und ein Anbieter ohne Modelliste wird gar nicht erst gefragt: die
    // Antwort waere immer leer, und der Hinweis darunter sagt bereits, warum.
    expect(aiApi.listCatalogModels).not.toHaveBeenCalled()
    expect(screen.getByText(/keine Modelliste/i)).toBeInTheDocument()
    // Das Modell bleibt deshalb ein Textfeld — der Deployment-Name gehoert
    // hinein, und den kennt nur der Betreiber.
    expect(screen.getByLabelText(/Standardmodell/i).tagName).toBe('INPUT')

    fireEvent.change(feld, { target: { value: 'mein-ai-hub' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(7, expect.objectContaining({
      azure_resource_name: 'mein-ai-hub',
    })))
  })

  it('schickt den Ressourcennamen nicht an einen Anbieter, der keinen hat', async () => {
    // Sonst stuende in der Zeile eines OpenRouter-Zugangs eine Angabe, die er
    // nie verwendet — und beim naechsten Blick in die Datenbank saehe sie aus
    // wie eine Einstellung.
    render(<AiProvidersSettings canWrite />)

    expect(await screen.findByLabelText('API-Key')).toBeInTheDocument()
    expect(screen.queryByLabelText('Azure-Ressourcenname')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalled())
    const [, nutzlast] = vi.mocked(aiApi.updateProvider).mock.calls[0]
    expect(nutzlast).not.toHaveProperty('azure_resource_name')
  })

  it('zeigt kein hoerendes Modell bei einem Anbieter, der nicht zuhoert', async () => {
    // Der Sprachmodus ueberspringt einen Zugang ohne `gehoer_wege` — gleich
    // was in diesem Feld steht. Ein ausfuellbares Feld waere also eine Zusage
    // ohne Deckung, und ein alter Wert darin blieb unsichtbar bestehen.
    vi.mocked(aiApi.listProviderSettings).mockResolvedValue([{
      ...provider,
      id: 7,
      name: 'Azure',
      provider_kind: 'azure_openai',
      base_url: null,
      default_model: 'gpt-5.1',
      azure_resource_name: 'mein-ai-hub',
      transcription_model: 'whisper-1',
    }])
    render(<AiProvidersSettings canWrite />)

    expect(await screen.findByLabelText('Azure-Ressourcenname')).toHaveValue('mein-ai-hub')
    expect(screen.queryByLabelText('Modell für Gesprochenes')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(7, expect.objectContaining({
      transcription_model: null,
    })))
  })

  it('zeigt OpenAI-Realtime ausschließlich mit Design-DNA-Auswahlen und speichert alle Preise', async () => {
    vi.mocked(aiApi.listProviderSettings).mockResolvedValue([{
      ...provider,
      provider_kind: 'openai',
      base_url: 'https://api.openai.com/v1',
      default_model: 'gpt-4.1',
      realtime_default: true,
      realtime_model: 'gpt-realtime',
      realtime_voice: 'marin',
      realtime_language: 'de',
      realtime_vad_eagerness: 'high',
      realtime_text_input_price_micro_usd_per_million: 1_000_000,
      realtime_text_output_price_micro_usd_per_million: 2_000_000,
      realtime_audio_input_price_micro_usd_per_million: 3_000_000,
      realtime_audio_output_price_micro_usd_per_million: 4_000_000,
    }])
    vi.mocked(aiApi.listCatalogModels).mockResolvedValue([
      {
        model_id: 'gpt-4.1', name: 'GPT-4.1', reasoning: false, efforts: [],
        default_effort: null, mandatory: false, recommended: false, vision: null,
      },
      {
        model_id: 'gpt-realtime', name: 'GPT Realtime', reasoning: false, efforts: [],
        default_effort: null, mandatory: false, recommended: false, vision: null,
      },
    ])

    const { container } = render(<AiProvidersSettings canWrite />)

    await waitFor(() => expect(screen.getByLabelText('Realtime-Modell')).toHaveTextContent('gpt-realtime'))
    expect(screen.getByLabelText('Realtime-Modell').tagName).toBe('BUTTON')
    expect(screen.getByLabelText('OpenAI-Stimme').tagName).toBe('BUTTON')
    expect(screen.getByLabelText('Antwortsprache').tagName).toBe('BUTTON')
    expect(screen.getByLabelText('Reaktion auf Sprechpausen').tagName).toBe('BUTTON')
    expect(container.querySelector('select')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(4, expect.objectContaining({
      realtime_default: true,
      realtime_model: 'gpt-realtime',
      realtime_voice: 'marin',
      realtime_language: 'de',
      realtime_vad_eagerness: 'high',
      realtime_text_input_price_micro_usd_per_million: 1_000_000,
      realtime_text_output_price_micro_usd_per_million: 2_000_000,
      realtime_audio_input_price_micro_usd_per_million: 3_000_000,
      realtime_audio_output_price_micro_usd_per_million: 4_000_000,
    })))
  })

  it('zeigt die Denkstufe nur für die Realtime-2-Reihe und speichert sie', async () => {
    vi.mocked(aiApi.listProviderSettings).mockResolvedValue([{
      ...provider,
      provider_kind: 'openai',
      realtime_default: true,
      realtime_model: 'gpt-realtime-2',
      realtime_voice: 'marin',
      realtime_reasoning_effort: 'medium',
      realtime_text_input_price_micro_usd_per_million: 1,
      realtime_text_output_price_micro_usd_per_million: 1,
      realtime_audio_input_price_micro_usd_per_million: 1,
      realtime_audio_output_price_micro_usd_per_million: 1,
    }])
    vi.mocked(aiApi.listCatalogModels).mockResolvedValue([{
      model_id: 'gpt-realtime-2', name: 'GPT Realtime 2', reasoning: true, efforts: ['low', 'medium', 'high'],
      default_effort: 'medium', mandatory: false, recommended: true, vision: null,
    }])

    render(<AiProvidersSettings canWrite />)
    const reasoning = await screen.findByLabelText('Denkstufe')
    expect(reasoning).toHaveTextContent('Mittel')
    expect(reasoning).not.toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(4, expect.objectContaining({
      realtime_reasoning_effort: 'medium',
    })))
  })

  it('holt die Denkstufen einzeln, wo es keinen Katalog gibt', async () => {
    // Die Luecke, die den Worker eines Azure-Zugangs stumpf liess: die
    // Stufenauswahl haengt an der Katalogliste, und die ist dort leer. Der
    // Chat wusste es laengst besser — dasselbe Modell hatte je nach Bildschirm
    // Stufen oder keine.
    vi.mocked(aiApi.listProviderSettings).mockResolvedValue([{
      ...provider,
      id: 7,
      name: 'Azure',
      provider_kind: 'azure_openai',
      base_url: null,
      default_model: 'gpt-5.6-luna',
      worker_model: 'gpt-5.6-luna',
      azure_resource_name: 'mein-ai-hub',
    }])
    vi.mocked(aiApi.findCatalogModel).mockResolvedValue({
      model_id: 'gpt-5.6-luna',
      name: 'GPT-5.6 Luna',
      reasoning: true,
      efforts: ['low', 'medium', 'high'],
      default_effort: 'medium',
      mandatory: false,
      recommended: false,
      vision: null,
    })
    render(<AiProvidersSettings canWrite />)

    // Der Katalog wird gar nicht erst gefragt — die Antwort waere immer leer.
    expect(await screen.findByLabelText('Azure-Ressourcenname')).toBeInTheDocument()
    expect(aiApi.listCatalogModels).not.toHaveBeenCalled()

    await waitFor(() =>
      expect(aiApi.findCatalogModel).toHaveBeenCalledWith('azure_openai', 'gpt-5.6-luna'))

    // Und damit steht die feste Stufe des Workers zur Wahl.
    const stufe = await screen.findByLabelText('Feste Denkstufe')
    expect(stufe).toBeInTheDocument()

    // Das Modell bleibt trotzdem ein Textfeld: ein einzelner Treffer ist kein
    // Katalog, und ein Auswahlfeld mit genau einem Eintrag naehme dem
    // Betreiber die Moeglichkeit, seinen Deployment-Namen einzutragen.
    expect(screen.getByLabelText(/Standardmodell/i).tagName).toBe('INPUT')
  })

  describe('GPT-Live', () => {
    // So, wie `/models` sie am 23.09.2026 für einen echten OpenAI-Zugang
    // lieferte: `none` steht nie unter `efforts` (`waehlbare_stufen` lässt es
    // aus), sondern als `mandatory: false` daneben.
    const LUNA = {
      model_id: 'gpt-6-luna', name: 'GPT-6 Luna', reasoning: true,
      efforts: ['low', 'medium', 'high', 'xhigh', 'max'], default_effort: 'medium',
      mandatory: false, recommended: false, vision: true,
    }
    const MINI = {
      model_id: 'gpt-5-mini', name: 'GPT-5 mini', reasoning: true,
      efforts: ['minimal', 'low', 'medium', 'high'], default_effort: 'medium',
      mandatory: true, recommended: false, vision: null,
    }
    const sprachmodell = (model_id: string) => ({
      model_id, name: model_id, reasoning: null, efforts: [], default_effort: null,
      mandatory: false, recommended: false, vision: null,
    })
    const LIVE_ZUGANG: AiProviderAdmin = {
      ...provider,
      provider_kind: 'openai',
      base_url: 'https://api.openai.com/v1',
      default_model: 'gpt-6-luna',
      realtime_default: true,
      realtime_model: 'gpt-live-1',
      realtime_voice: 'beacon',
      realtime_reasoning_effort: 'xhigh',
      realtime_backend_model: null,
      realtime_minute_price_micro_usd: 50_000,
      realtime_text_input_price_micro_usd_per_million: 1_000_000,
      realtime_text_output_price_micro_usd_per_million: 8_000_000,
    }

    beforeEach(() => {
      vi.mocked(aiApi.listProviderSettings).mockResolvedValue([LIVE_ZUGANG])
      vi.mocked(aiApi.listCatalogModels).mockResolvedValue([
        LUNA,
        MINI,
        sprachmodell('gpt-live-1'),
        sprachmodell('gpt-live-transcribe'),
        sprachmodell('gpt-realtime-2'),
      ])
    })

    it('zeigt Backend-Modell und Minutenpreis statt Audiopreisen und Pausenerkennung', async () => {
      // GPT-Live rechnet nach Sekunden ab („billed per second") und hat keine
      // turn_detection: ein Audiopreis oder eine Pausenerkennung wären hier
      // Felder ohne Wirkung.
      render(<AiProvidersSettings canWrite />)

      await waitFor(() => expect(screen.getByLabelText('Realtime-Modell')).toHaveTextContent('gpt-live-1'))
      expect(screen.getByRole('heading', { name: 'GPT-Live' })).toBeInTheDocument()
      expect(screen.getByLabelText('GPT-Live-Stimme')).toHaveTextContent('beacon')
      // Die Auswahl entsteht erst mit dem Katalog; vorher steht ein Textfeld da.
      await waitFor(() =>
        expect(screen.getByLabelText('Backend-Modell')).toHaveTextContent('Wie Standardmodell (gpt-6-luna)'))
      expect(screen.getByLabelText('Denkstufe')).toHaveTextContent('Sehr hoch')
      expect(screen.getByLabelText('Sprachsitzung je Minute')).toBeInTheDocument()
      expect(screen.getByLabelText('Backend-Modell: Eingabe je 1 Mio. Tokens')).toBeInTheDocument()
      expect(screen.queryByLabelText('Reaktion auf Sprechpausen')).not.toBeInTheDocument()
      expect(screen.queryByLabelText('Audio-Eingabe je 1 Mio. Tokens')).not.toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
      await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(4, expect.objectContaining({
        realtime_default: true,
        realtime_model: 'gpt-live-1',
        realtime_voice: 'beacon',
        realtime_reasoning_effort: 'xhigh',
        realtime_backend_model: null,
        realtime_minute_price_micro_usd: 50_000,
        realtime_text_input_price_micro_usd_per_million: 1_000_000,
        realtime_text_output_price_micro_usd_per_million: 8_000_000,
      })))
    })

    it('bietet nur Denkstufen, die GPT-Live annimmt und das Backend-Modell führt', async () => {
      // „Supported values depend on the backend model" (OpenAI-Referenz zu
      // `delegation.responses.reasoning.effort`). GPT-6 Luna führt kein
      // `minimal`, GPT-Live nimmt kein `max`, GPT-5 mini kein `xhigh` — und
      // wechselt das Backend-Modell, fällt eine Stufe weg, die das neue nicht
      // kennt. `none` erlaubt OpenAIs GPT-6-Leitfaden für Luna ausdrücklich
      // („GPT-6 Sol and Luna support `none`"), GPT-5 mini denkt zwingend.
      const angebot = () => screen.getAllByRole('option').map((option) => option.textContent)
      render(<AiProvidersSettings canWrite />)

      const stufe = await screen.findByLabelText('Denkstufe')
      await waitFor(() => expect(stufe).toHaveTextContent('Sehr hoch'))
      fireEvent.click(stufe)
      expect(await screen.findByRole('option', { name: /Kein Nachdenken/ })).toBeInTheDocument()
      expect(angebot()).toEqual([
        'Vorgabe des Backend-Modells', 'Kein Nachdenken', 'Niedrig', 'Mittel', 'Hoch', 'Sehr hoch',
      ])
      fireEvent.click(screen.getByRole('option', { name: /Sehr hoch/ }))

      fireEvent.click(screen.getByLabelText('Backend-Modell'))
      // Ein Sprachmodell denkt nicht hinter einem anderen nach.
      expect(screen.queryByRole('option', { name: /^gpt-live-1/ })).not.toBeInTheDocument()
      fireEvent.click(await screen.findByRole('option', { name: /^gpt-5-mini/ }))
      await waitFor(() =>
        expect(screen.getByLabelText('Denkstufe')).toHaveTextContent('Vorgabe des Backend-Modells'))
      fireEvent.click(screen.getByLabelText('Denkstufe'))
      await screen.findByRole('option', { name: /^Minimal/ })
      expect(angebot()).toEqual(['Vorgabe des Backend-Modells', 'Minimal', 'Niedrig', 'Mittel', 'Hoch'])
      fireEvent.click(screen.getByRole('option', { name: /Vorgabe des Backend-Modells/ }))

      fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
      await waitFor(() => expect(aiApi.updateProvider).toHaveBeenCalledWith(4, expect.objectContaining({
        realtime_backend_model: 'gpt-5-mini',
        realtime_reasoning_effort: null,
      })))
    })

    it('führt kein gpt-live-transcribe als Sprachmodell', async () => {
      // Teilt den Namen, führt `v1/live/sessions` laut seiner Modellseite aber
      // ausdrücklich nicht — dieselbe Ausnahme wie in `sprachwege.py`.
      render(<AiProvidersSettings canWrite />)

      const modell = await screen.findByLabelText('Realtime-Modell')
      await waitFor(() => expect(modell).toHaveTextContent('gpt-live-1'))
      fireEvent.click(modell)
      expect(await screen.findByRole('option', { name: /^gpt-realtime-2/ })).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: /gpt-live-transcribe/ })).not.toBeInTheDocument()
    })

    it('nimmt beim Wechsel zu Realtime zurück, was nur GPT-Live kennt', async () => {
      // `beacon` gibt es nur bei GPT-Live, `xhigh` nimmt Realtime-2 nicht an.
      // Beides fällt beim Wechsel weg, statt erst beim Speichern abgewiesen zu
      // werden — und mit dem Weg wechseln die Felder.
      render(<AiProvidersSettings canWrite />)

      const modell = await screen.findByLabelText('Realtime-Modell')
      await waitFor(() => expect(modell).toHaveTextContent('gpt-live-1'))
      fireEvent.click(modell)
      fireEvent.click(await screen.findByRole('option', { name: /^gpt-realtime-2/ }))

      await waitFor(() => expect(screen.getByLabelText('OpenAI-Stimme')).toHaveTextContent('Stimme wählen …'))
      expect(screen.getByLabelText('Denkstufe')).toHaveTextContent('Keine Denkstufe')
      expect(screen.getByRole('heading', { name: 'OpenAI Realtime' })).toBeInTheDocument()
      expect(screen.getByLabelText('Reaktion auf Sprechpausen')).toBeInTheDocument()
      expect(screen.getByLabelText('Audio-Eingabe je 1 Mio. Tokens')).toBeInTheDocument()
      expect(screen.queryByLabelText('Sprachsitzung je Minute')).not.toBeInTheDocument()
      expect(screen.queryByLabelText('Backend-Modell')).not.toBeInTheDocument()
    })
  })
})
