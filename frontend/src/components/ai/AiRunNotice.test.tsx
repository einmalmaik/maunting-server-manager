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
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AI_RUHENDE_LAUFZUSTAENDE, aiApi, type AiRunInfo } from '@/api/ai'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiRunNotice } from './AiRunNotice'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return { ...original, aiApi: { getActiveRun: vi.fn() } }
})

const lauf = (status: AiRunInfo['status']): AiRunInfo => ({
  id: 'lauf-1', status, stop_reason: null, message_id: 'msg-1',
  live: true, created_at: '2026-08-10T12:00:00Z',
})

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

function zeichnen(pfad: string) {
  return render(
    <MemoryRouter initialEntries={[pfad]}>
      <AiRunNotice />
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
    vi.mocked(aiApi.getActiveRun)
      .mockResolvedValueOnce(lauf('running'))
      .mockResolvedValue(lauf('waiting_confirmation'))

    zeichnen('/notes')

    // Erste Beobachtung: nichts zu melden, der Lauf arbeitet.
    await waitFor(() => expect(aiApi.getActiveRun).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(9_000)
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Die KI wartet auf deine Bestätigung.')
    })
  })

  it('schweigt, solange man im Chat steht', async () => {
    // Dort sieht man es ohnehin — eine Meldung wäre nur im Weg.
    vi.mocked(aiApi.getActiveRun)
      .mockResolvedValueOnce(lauf('running'))
      .mockResolvedValue(lauf('waiting_confirmation'))

    zeichnen('/ai')

    await waitFor(() => expect(aiApi.getActiveRun).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(9_000)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('fragt gar nicht erst nach, wenn die KI-Meldungen abgeschaltet sind', async () => {
    // Der Schalter an der Glocke. Er soll nicht nur die Anzeige unterdrücken,
    // sondern auch die Nachfragen — sonst pollte das Panel für nichts.
    nutzer(false)
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(lauf('running'))

    zeichnen('/notes')

    await vi.advanceTimersByTimeAsync(9_000)
    expect(aiApi.getActiveRun).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('hört auf nachzusehen, wenn nichts mehr läuft', async () => {
    // Ein Dauerpoller für einen ruhenden Lauf wäre Last ohne Gegenwert.
    vi.mocked(aiApi.getActiveRun).mockResolvedValue(null)

    zeichnen('/notes')

    await waitFor(() => expect(aiApi.getActiveRun).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(30_000)
    expect(aiApi.getActiveRun).toHaveBeenCalledTimes(1)
  })

  it('meldet jeden ruhenden Zustand, auch einen später hinzugekommenen', async () => {
    // Der Grund für die gemeinsame Liste in `api/ai.ts`: „ruht" ist eine
    // Aussage über den Lauf, keine über diese Komponente. Stünde hier wieder
    // eine eigene Aufzählung und käme im Vorrat ein Zustand dazu — etwa ein
    // „expired" für abgelaufene Bestätigungen —, läutete die Glocke für ihn
    // nie: außerhalb des Chats erführe niemand, dass die KI wartet. Der Test
    // läuft deshalb über die geteilte Liste und wächst mit ihr.
    for (const zustand of AI_RUHENDE_LAUFZUSTAENDE) {
      vi.mocked(aiApi.getActiveRun).mockReset()
        .mockResolvedValueOnce(lauf('running'))
        .mockResolvedValue(lauf(zustand))

      const { unmount } = zeichnen('/notes')
      await waitFor(() => expect(aiApi.getActiveRun).toHaveBeenCalledTimes(1))
      await vi.advanceTimersByTimeAsync(9_000)
      await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument())
      unmount()
    }
  })
})
