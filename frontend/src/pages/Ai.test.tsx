import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiVoiceConfig } from '@/api/ai'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { Ai } from './Ai'

/**
 * Die KI-Seite als Rahmen: welcher Modus was zeigt.
 *
 * Anlass fuer diese Datei ist ein Befund aus der Runde, in der der
 * Autonomie-Schalter in den Sprachmodus kam. Er wurde hier eingehaengt, aber
 * nicht aus `AiChat` entfernt — im getippten Modus standen danach **zwei**
 * Schalter nebeneinander, jeder mit eigenem Zustand aus einem eigenen
 * `listAutonomyGrants()`. Wer den einen umlegte, sah am anderen weiter den
 * alten Stand und schaltete mit dem naechsten Klick zurueck.
 *
 * Gesehen hat das ein Mensch beim Gegenlesen, kein Test: die Seite hatte
 * keinen. Genau deshalb gibt es sie jetzt.
 */

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return { ...original, aiApi: { getVoiceConfig: vi.fn() } }
})

vi.mock('@/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/client')>()
  return { ...original, api: vi.fn() }
})

// Die beiden Modi sind hier Attrappen: geprueft wird, **welcher** erscheint und
// was daneben steht — nicht, was in ihnen passiert. Dafuer haben beide eigene,
// ausfuehrliche Testdateien.
vi.mock('@/components/ai/AiChat', () => ({
  AiChat: () => <div>chat-attrappe</div>,
}))
vi.mock('@/components/ai/voice/SprachAnsicht', () => ({
  SprachAnsicht: () => <div>sprache-attrappe</div>,
}))
vi.mock('@/components/ai/AiSkillDirectory', () => ({
  AiSkillDirectory: () => null,
}))
vi.mock('@/components/ai/GuardianAnsicht', () => ({
  GuardianAnsicht: () => <div>guardian-attrappe</div>,
}))
vi.mock('@/components/ai/WorkerAnsicht', () => ({
  WorkerAnsicht: ({ conversationId }: { conversationId: string }) => (
    <div>{`worker-attrappe:${conversationId}`}</div>
  ),
}))

// Der Schalter zaehlt hier als Vorkommen und nicht als Bedienelement. Die echte
// Komponente laedt beim Zeichnen ihre Freigaben — genau der Nebeneffekt, der
// den doppelten Knopf so tueckisch machte.
vi.mock('@/components/ai/AiAutonomyButton', () => ({
  AiAutonomyButton: () => <div data-testid="autonomie-schalter" />,
}))

const KONFIGURATION: AiVoiceConfig = {
  available: true,
  model: 'openai/gpt-5.6',
  voice: '21m00Tcm4TlvDq8ikWAM',
  sample_rate: 24_000,
  max_seconds: 900,
}

function rechte(...global_keys: string[]) {
  usePermissionsStore.setState({
    me: {
      is_owner: false, role_id: null, role_name: null,
      global_keys, server_keys: {},
    },
    isLoading: false,
    error: null,
  })
}

/**
 * Die Seite braucht einen Router.
 *
 * Seit das Guardian-Fenster dazugekommen ist, steht die gewaehlte Ansicht in
 * der Adresse (`?ansicht=guardian`) statt in einem `useState` — das ist der
 * Weg, ueber den der Guardian-Reiter eines Servers und die Glocke hierher
 * zeigen. Ein Zustand allein in der Komponente waere von dort nicht
 * erreichbar, und `useSearchParams` wirft ohne Router.
 */
function zeichnen(pfad = '/ai') {
  render(
    <MemoryRouter initialEntries={[pfad]}>
      <Ai />
    </MemoryRouter>,
  )
}

async function inDenSprachmodus() {
  fireEvent.click(await screen.findByRole('button', { name: i18n.t('ai.voice.toVoiceMode') }))
  await screen.findByText('sprache-attrappe')
}

describe('Ai', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.clearAllMocks()
    vi.mocked(aiApi.getVoiceConfig).mockResolvedValue(KONFIGURATION)
    vi.mocked(client.api).mockResolvedValue([])
  })

  it('zeigt die Worker-Ansicht nur mit Kennung in der Adresse', async () => {
    // `?ansicht=worker` allein ist keine Ansicht: es gibt beliebig viele
    // Auftraege, und ein Fenster ohne Kennung wuesste nicht, wessen Verlauf
    // es zeigt. In der Adresse steht nur die UUID — nie Titel oder Inhalt.
    rechte('ai.chat.use')
    zeichnen('/ai?ansicht=worker&id=konv-worker-1')

    await screen.findByText('worker-attrappe:konv-worker-1')
    expect(screen.queryByText('chat-attrappe')).toBeNull()
  })

  it('faellt ohne Worker-Kennung auf den Chat zurueck', async () => {
    rechte('ai.chat.use')
    zeichnen('/ai?ansicht=worker')

    await screen.findByText('chat-attrappe')
    expect(screen.queryByText(/worker-attrappe/)).toBeNull()
  })

  it('zeigt im getippten Modus keinen zweiten Autonomie-Schalter', async () => {
    // `AiChat` bringt seinen eigenen mit. Ein zweiter daneben waere kein
    // Komfort, sondern zwei Wahrheiten ueber denselben Zustand.
    rechte('ai.chat.use', 'ai.voice.use', 'ai.autonomous.use')
    zeichnen()

    await screen.findByText('chat-attrappe')
    expect(screen.queryByTestId('autonomie-schalter')).toBeNull()
  })

  it('zeigt ihn im Sprachmodus, wo der Chat keinen mehr hat', async () => {
    // Dort ist er noetig: jede Rueckfrage zwingt den Sprechenden sonst, mitten
    // im Gespraech auf den Bildschirm zu sehen.
    rechte('ai.chat.use', 'ai.voice.use', 'ai.autonomous.use')
    zeichnen()
    await inDenSprachmodus()

    expect(screen.getByTestId('autonomie-schalter')).toBeTruthy()
  })

  it('holt die Serverliste erst im Sprachmodus', async () => {
    // Im getippten Modus holt `AiChat` sie fuer seinen eigenen Schalter. Zwei
    // Abrufe derselben Liste fuer zwei Knoepfe waren der sichtbare Teil des
    // Fehlers; dies ist der unsichtbare.
    rechte('ai.chat.use', 'ai.voice.use', 'ai.autonomous.use')
    zeichnen()

    await screen.findByText('chat-attrappe')
    expect(client.api).not.toHaveBeenCalledWith('/servers')

    await inDenSprachmodus()
    await waitFor(() => expect(client.api).toHaveBeenCalledWith('/servers'))
  })

  it('zeigt ihn ohne Autonomierecht auch im Sprachmodus nicht', async () => {
    rechte('ai.chat.use', 'ai.voice.use')
    zeichnen()
    await inDenSprachmodus()

    expect(screen.queryByTestId('autonomie-schalter')).toBeNull()
    expect(client.api).not.toHaveBeenCalledWith('/servers')
  })

  it('zeigt ohne eingerichteten Sprachzugang keinen Sprachknopf', async () => {
    // Kein ausgegrauter Umschalter, sondern keiner. Dieselbe Regel wie bei
    // `web_search`: was der Betreiber nicht bestellt hat, gibt es nicht.
    //
    // Die Kopfleiste selbst bleibt seit dem Guardian-Fenster stehen: dort
    // wechselt man auch ohne Sprachzugang hinueber, und sie zu verstecken
    // haette den einzigen Weg dorthin an eine Einstellung gehaengt, die nichts
    // damit zu tun hat.
    vi.mocked(aiApi.getVoiceConfig).mockResolvedValue({ ...KONFIGURATION, available: false })
    rechte('ai.chat.use', 'ai.voice.use', 'ai.autonomous.use')
    zeichnen()

    await screen.findByText('chat-attrappe')
    expect(screen.queryByRole('button', { name: i18n.t('ai.voice.toVoiceMode') })).toBeNull()
    expect(screen.queryByTestId('autonomie-schalter')).toBeNull()
  })
})
