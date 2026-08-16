/**
 * Die Meldung unten rechts.
 *
 * Sie ist der Gegenwert dazu, dass die KI im Hintergrund weiterarbeitet: wer
 * einen Auftrag gibt und dann auf eine andere Seite geht, soll erfahren, wenn
 * er fertig ist oder auf eine Bestätigung wartet. Ohne sie müsste man den Chat
 * offen lassen — also genau das tun, was nicht mehr nötig sein soll.
 *
 * Gemeldet wird der **Übergang**, nicht der Zustand. Sonst käme bei jedem Takt
 * dieselbe Meldung erneut.
 *
 * Seit es zwei Fenster gibt, wird je Fenster nachgesehen und je Fenster
 * gemeldet. Beides ist nötig: es können zwei Läufe gleichzeitig arbeiten (der
 * Mensch fragt etwas, während im Hintergrund ein Server repariert wird), und
 * für eine Reparatur ist „Die KI ist mit deinem Auftrag fertig" schlicht
 * falsch — den Auftrag hat niemand gegeben.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AI_RUHENDE_LAUFZUSTAENDE,
  aiApi,
  type AiConversationKind,
  type AiRunInfo,
} from '@/api/ai'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiRunNotice } from './AiRunNotice'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return { ...original, aiApi: { getActiveRun: vi.fn() } }
})

const lauf = (
  status: AiRunInfo['status'],
  kind: AiConversationKind = 'primary',
): AiRunInfo => ({
  id: `lauf-${kind}`, status, stop_reason: null, message_id: 'msg-1',
  live: true, created_at: '2026-08-10T12:00:00Z',
  kind, conversation_id: `konv-${kind}`, server_id: null,
})

/**
 * Antworten je Fenster festlegen — eine Folge je Art, der letzte Wert bleibt.
 *
 * Die Glocke fragt beide Fenster in jedem Takt. Ein flaches
 * `mockResolvedValueOnce` träfe deshalb nicht den ersten *Takt*, sondern die
 * erste *Frage*, und damit abwechselnd das falsche Fenster.
 */
function antworten(plan: Partial<Record<AiConversationKind, (AiRunInfo | null)[]>>) {
  const rest: Record<string, (AiRunInfo | null)[]> = {
    primary: [...(plan.primary ?? [null])],
    guardian: [...(plan.guardian ?? [null])],
  }
  vi.mocked(aiApi.getActiveRun).mockReset().mockImplementation(async (kind) => {
    const folge = rest[kind ?? 'primary']
    return folge.length > 1 ? (folge.shift() ?? null) : (folge[0] ?? null)
  })
}

/** Wie oft ein bestimmtes Fenster gefragt wurde. */
function fragen(kind: AiConversationKind): number {
  return vi.mocked(aiApi.getActiveRun).mock.calls.filter(([art]) => art === kind).length
}

function nutzer(aiNotifications: boolean) {
  useAuthStore.setState({
    user: {
      id: 1, username: 'owner', email: 'owner@example.test', is_owner: true,
      is_active: true, email_verified: true, two_factor_enabled: false,
      email_notifications: true, ai_notifications: aiNotifications,
      role_id: null, created_at: '2026-05-31T00:00:00Z',
    },
    isAuthenticated: true,
    isLoading: false,
  })
}

/** Verrät, wo der Router gerade steht — sonst liesse sich „Zum Assistenten" nur zählen, nicht prüfen. */
function Standort() {
  const ort = useLocation()
  return <span data-testid="standort">{`${ort.pathname}${ort.search}`}</span>
}

function zeichnen(pfad: string) {
  return render(
    <MemoryRouter initialEntries={[pfad]}>
      <AiRunNotice />
      <Standort />
    </MemoryRouter>,
  )
}

describe('AiRunNotice', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    nutzer(true)
    usePermissionsStore.setState({
      me: {
        is_owner: false, role_id: null, role_name: null,
        global_keys: ['ai.chat.use'], server_keys: {},
      },
      isLoading: false,
      error: null,
    })
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.mocked(aiApi.getActiveRun).mockReset()
  })

  it('meldet, wenn ein laufender Auftrag auf eine Bestätigung wartet', async () => {
    antworten({ primary: [lauf('running'), lauf('waiting_confirmation')] })

    zeichnen('/notes')

    // Erste Beobachtung: nichts zu melden, der Lauf arbeitet.
    await waitFor(() => expect(fragen('primary')).toBe(1))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(9_000)
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Die KI wartet auf deine Bestätigung.')
    })
  })

  it('schweigt, solange man im Chat steht — und fragt auch nicht nach', async () => {
    // Dort sieht man es ohnehin — eine Meldung wäre nur im Weg. Also darf der
    // schnelle Takt dort auch nicht laufen: er könnte per Konstruktion nichts
    // anzeigen und käme obendrauf auf den Ereignisstrom, der denselben Zustand
    // schon liefert. Deshalb zählt dieser Test die Aufrufe, nicht nur die
    // ausbleibende Anzeige.
    antworten({ primary: [lauf('running'), lauf('waiting_confirmation')] })

    zeichnen('/ai')

    await waitFor(() => expect(fragen('primary')).toBe(1))
    await vi.advanceTimersByTimeAsync(9_000)
    expect(fragen('primary')).toBe(1)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('meldet eine Reparatur auch dem, der gerade im Chat steht', async () => {
    // **Der Fall, den die alte Unterdrückung verschluckt hat.** Sie ging über
    // den Pfad `/ai` und kannte nur ein Fenster; wer im Chat stand, erfuhr von
    // einer beendeten Reparatur nichts — obwohl er sie dort gar nicht sehen
    // konnte.
    antworten({
      primary: [null],
      guardian: [lauf('running', 'guardian'), lauf('completed', 'guardian')],
    })

    zeichnen('/ai')

    await waitFor(() => expect(fragen('guardian')).toBe(1))
    await vi.advanceTimersByTimeAsync(61_000)
    await waitFor(() => {
      expect(screen.getByRole('status'))
        .toHaveTextContent('Die KI hat eine Guardian-Störung bearbeitet.')
    })
  })

  it('schweigt, wenn man in das Guardian-Fenster sieht', async () => {
    antworten({
      guardian: [lauf('running', 'guardian'), lauf('completed', 'guardian')],
    })

    zeichnen('/ai?ansicht=guardian')

    await waitFor(() => expect(fragen('guardian')).toBe(1))
    await vi.advanceTimersByTimeAsync(61_000)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('nennt eine Reparatur nicht "deinen Auftrag"', async () => {
    // Den Auftrag hat niemand gegeben — eine Störung hat ihn ausgelöst. Der
    // Satz aus dem Chat wäre hier eine Behauptung über etwas, das der
    // Betreiber nie getan hat.
    antworten({
      guardian: [lauf('running', 'guardian'), lauf('waiting_confirmation', 'guardian')],
    })

    zeichnen('/notes')

    await waitFor(() => expect(fragen('guardian')).toBe(1))
    await vi.advanceTimersByTimeAsync(9_000)
    await waitFor(() => {
      const meldung = screen.getByRole('status')
      expect(meldung).toHaveTextContent('Eine Guardian-Reparatur wartet auf deine Freigabe.')
      expect(meldung).not.toHaveTextContent('deinem Auftrag')
    })
  })

  it('führt bei einer Reparatur in das Guardian-Fenster, nicht in den Chat', async () => {
    // Der Verweis muss die Art der Meldung kennen: `/ai` allein öffnete den
    // Dauerchat, in dem von dieser Reparatur keine Zeile steht — man stünde
    // vor einem leeren Verlauf und hielte die Meldung für einen Fehler.
    antworten({
      guardian: [lauf('running', 'guardian'), lauf('completed', 'guardian')],
    })

    zeichnen('/notes')

    await waitFor(() => expect(fragen('guardian')).toBe(1))
    await vi.advanceTimersByTimeAsync(9_000)
    fireEvent.click(await screen.findByRole('button', { name: 'Zum Assistenten' }))
    await waitFor(() => {
      expect(screen.getByTestId('standort')).toHaveTextContent('/ai?ansicht=guardian')
    })
  })

  it('führt bei einem Chatlauf weiterhin in den Chat', async () => {
    antworten({ primary: [lauf('running'), lauf('completed')] })

    zeichnen('/notes')

    await waitFor(() => expect(fragen('primary')).toBe(1))
    await vi.advanceTimersByTimeAsync(9_000)
    fireEvent.click(await screen.findByRole('button', { name: 'Zum Assistenten' }))
    await waitFor(() => {
      expect(screen.getByTestId('standort')).toHaveTextContent(/^\/ai$/)
    })
  })

  it('fragt gar nicht erst nach, wenn die KI-Meldungen abgeschaltet sind', async () => {
    // Der Schalter an der Glocke. Er soll nicht nur die Anzeige unterdrücken,
    // sondern auch die Nachfragen — sonst pollte das Panel für nichts.
    nutzer(false)
    antworten({ primary: [lauf('running')] })

    zeichnen('/notes')

    await vi.advanceTimersByTimeAsync(9_000)
    expect(aiApi.getActiveRun).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('sieht im Ruhetakt weiter nach, ohne dabei zu pollen wie im Betrieb', async () => {
    // **Hier stand einmal das Gegenteil**: „hört auf nachzusehen, wenn nichts
    // mehr läuft". Das war richtig, solange ein Lauf nur durch eine getippte
    // Nachricht entstehen konnte — wer tippt, ist im Chat, und dort meldet der
    // Ereignisstrom selbst.
    //
    // Seit ein stehender Auftrag um acht Uhr von selbst anfängt, stimmt die
    // Annahme nicht mehr: eine Seite, die seit gestern Abend offen steht,
    // hätte den Lauf nie bemerkt. Der Ruhetakt ist der einzige Weg, auf dem
    // sie davon erfährt.
    antworten({ primary: [null], guardian: [null] })

    zeichnen('/notes')

    await waitFor(() => expect(fragen('primary')).toBe(1))
    // Nach dem schnellen Takt passiert noch nichts — sonst wäre der Ruhetakt
    // gar keiner, sondern nur ein zweiter Betriebstakt.
    await vi.advanceTimersByTimeAsync(9_000)
    expect(fragen('primary')).toBe(1)

    await vi.advanceTimersByTimeAsync(55_000)
    await waitFor(() => expect(fragen('primary')).toBe(2))
  })

  it('meldet einen Lauf, den niemand ausgelöst hat', async () => {
    // Der Fall, für den der Ruhetakt existiert: die fällige KI-Aufgabe. Beim
    // ersten Nachsehen ist nichts los, eine Minute später arbeitet plötzlich
    // etwas, und am Ende soll die Glocke läuten.
    antworten({ primary: [null, lauf('running'), lauf('completed')] })

    zeichnen('/notes')

    await waitFor(() => expect(fragen('primary')).toBe(1))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    // Der Ruhetakt findet den von selbst gestarteten Lauf …
    await vi.advanceTimersByTimeAsync(61_000)
    await waitFor(() => expect(fragen('primary')).toBe(2))

    // … und der schnelle Takt danach seinen Abschluss.
    await vi.advanceTimersByTimeAsync(9_000)
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Die KI ist mit deinem Auftrag fertig.')
    })
  })

  it('meldet jeden ruhenden Zustand, auch einen später hinzugekommenen', async () => {
    // Der Grund für die gemeinsame Liste in `api/ai.ts`: „ruht" ist eine
    // Aussage über den Lauf, keine über diese Komponente. Stünde hier wieder
    // eine eigene Aufzählung und käme im Vorrat ein Zustand dazu — etwa ein
    // „expired" für abgelaufene Bestätigungen —, läutete die Glocke für ihn
    // nie: außerhalb des Chats erführe niemand, dass die KI wartet. Der Test
    // läuft deshalb über die geteilte Liste und wächst mit ihr.
    for (const zustand of AI_RUHENDE_LAUFZUSTAENDE) {
      antworten({ primary: [lauf('running'), lauf(zustand)] })

      const { unmount } = zeichnen('/notes')
      await waitFor(() => expect(fragen('primary')).toBe(1))
      await vi.advanceTimersByTimeAsync(9_000)
      await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument())
      unmount()
    }
  })
})
