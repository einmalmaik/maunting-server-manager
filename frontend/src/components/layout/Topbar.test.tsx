import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Topbar } from './Topbar'
import { useAuthStore } from '@/stores/authStore'
import { useToastStore } from '@/stores/toastStore'
import { api } from '@/api/client'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

function setUser(emailNotifications: boolean, aiNotifications = true) {
  useAuthStore.setState({
    user: {
      id: 1,
      username: 'owner',
      email: 'owner@example.test',
      is_owner: true,
      is_active: true,
      email_verified: true,
      two_factor_enabled: false,
      email_notifications: emailNotifications,
      ai_notifications: aiNotifications,
      role_id: null,
      created_at: '2026-05-31T00:00:00Z',
    },
    isAuthenticated: true,
    isLoading: false,
  })
}

function oeffneGlocke() {
  fireEvent.click(screen.getByRole('button', { name: 'E-Mail-Benachrichtigungen: aktiv' }))
}

describe('Topbar', () => {
  beforeEach(() => {
    i18n.changeLanguage('de')
    vi.mocked(api).mockReset().mockResolvedValue({})
    useToastStore.setState({ toasts: [] })
    setUser(true)
  })

  it('schaltet die E-Mail-Benachrichtigungen über die Glocke', async () => {
    // Vorher lag hinter der Glocke ein einzelner Schalter mit einem
    // Bestätigungsdialog. Jetzt öffnet sie ein Menü mit zwei Schaltern; die
    // Rückfrage ist weg, weil ein Schalter, den man mit einem zweiten Klick
    // zurückstellt, keine braucht.
    render(
      <MemoryRouter>
        <Topbar />
      </MemoryRouter>,
    )

    oeffneGlocke()
    fireEvent.click(screen.getByRole('switch', { name: 'E-Mail-Benachrichtigungen' }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/me/notifications?enabled=false', { method: 'PATCH' })
    })
    expect(useAuthStore.getState().user?.email_notifications).toBe(false)
  })

  it('schaltet die KI-Meldungen getrennt davon', async () => {
    // Der eigentliche Punkt der Erweiterung: die KI verschickt keine E-Mails.
    // Wer keine Post will, will deswegen nicht auch keinen Hinweis mehr, dass
    // ein laufender Auftrag auf seine Bestätigung wartet.
    render(
      <MemoryRouter>
        <Topbar />
      </MemoryRouter>,
    )

    oeffneGlocke()
    fireEvent.click(screen.getByRole('switch', { name: 'KI-Meldungen im Panel' }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/me/notifications?ai=false', { method: 'PATCH' })
    })
    expect(useAuthStore.getState().user?.ai_notifications).toBe(false)
    // Und die E-Mails sind unangetastet geblieben.
    expect(useAuthStore.getState().user?.email_notifications).toBe(true)
  })

  it('dreht den Schalter zurück, wenn das Speichern scheitert', async () => {
    // Sonst zeigte die Oberfläche einen Zustand an, den der Server nicht kennt —
    // und der nächste Neuladen würde ihn stillschweigend zurücksetzen.
    vi.mocked(api).mockRejectedValue(new Error('kaputt'))

    render(
      <MemoryRouter>
        <Topbar />
      </MemoryRouter>,
    )

    oeffneGlocke()
    const schalter = screen.getByRole('switch', { name: 'KI-Meldungen im Panel' })
    fireEvent.click(schalter)

    await waitFor(() => {
      expect(useToastStore.getState().toasts).toHaveLength(1)
    })
    expect(schalter).toHaveAttribute('aria-checked', 'true')
  })
})
