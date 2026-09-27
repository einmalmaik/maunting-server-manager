import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { PasswordTab } from './PasswordTab'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn() }
})

const t = (key: string) => i18n.t(key)

function setupUser(hasPassword = true) {
  useAuthStore.setState({
    user: {
      id: 1,
      username: 'test-user',
      email: 'test-user@example.invalid',
      is_owner: false,
      is_active: true,
      email_verified: true,
      two_factor_enabled: false,
      has_password: hasPassword,
      email_notifications: false,
      ai_notifications: false,
      role_id: null,
      created_at: '2026-08-28T00:00:00Z',
    },
  })
}

describe('PasswordTab (Passwort ändern / festlegen)', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset()
  })

  it('zeigt für Social-Login-Konten (has_password: false) "Passwort festlegen" ohne Altfeld', async () => {
    setupUser(false)
    render(<PasswordTab />)

    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(t('profile.setPassword'))
    expect(screen.getByText(t('profile.setPasswordInfo'))).toBeInTheDocument()
    expect(screen.queryByLabelText(t('profile.currentPassword'))).not.toBeInTheDocument()

    expect(screen.getByLabelText(t('profile.newPassword'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('profile.confirmPassword'))).toBeInTheDocument()
  })

  it('sendet bei Konten ohne Passwort an /auth/set-password und aktualisiert den Store', async () => {
    setupUser(false)
    vi.mocked(api).mockResolvedValueOnce({ message: 'Passwort festgelegt' })
    render(<PasswordTab />)

    fireEvent.change(screen.getByLabelText(t('profile.newPassword')), {
      target: { value: 'SicheresPasswort123!' },
    })
    fireEvent.change(screen.getByLabelText(t('profile.confirmPassword')), {
      target: { value: 'SicheresPasswort123!' },
    })

    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/set-password', {
        method: 'POST',
        body: JSON.stringify({
          new_password: 'SicheresPasswort123!',
          otp_code: null,
          passkey: null,
        }),
      })
      expect(useAuthStore.getState().user?.has_password).toBe(true)
    })
  })

  it('zeigt für reguläre Konten (has_password: true) "Passwort ändern" mit aktuellem Passwort', async () => {
    setupUser(true)
    vi.mocked(api).mockResolvedValueOnce({ message: 'Passwort geändert' })
    render(<PasswordTab />)

    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(t('profile.changePassword'))
    expect(screen.queryByText(t('profile.setPasswordInfo'))).not.toBeInTheDocument()
    expect(screen.getByLabelText(t('profile.currentPassword'))).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText(t('profile.currentPassword')), {
      target: { value: 'AltesPasswort123!' },
    })
    fireEvent.change(screen.getByLabelText(t('profile.newPassword')), {
      target: { value: 'NeuesSicheresPasswort123!' },
    })
    fireEvent.change(screen.getByLabelText(t('profile.confirmPassword')), {
      target: { value: 'NeuesSicheresPasswort123!' },
    })

    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          new_password: 'NeuesSicheresPasswort123!',
          otp_code: null,
          passkey: null,
          current_password: 'AltesPasswort123!',
        }),
      })
    })
  })

  it('zeigt Fehler bei nicht übereinstimmenden Passwörtern', async () => {
    setupUser(false)
    render(<PasswordTab />)

    fireEvent.change(screen.getByLabelText(t('profile.newPassword')), {
      target: { value: 'Passwort123!' },
    })
    fireEvent.change(screen.getByLabelText(t('profile.confirmPassword')), {
      target: { value: 'AnderesPasswort123!' },
    })

    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => {
      expect(screen.getByText(t('profile.passwordMismatch'))).toBeInTheDocument()
      expect(api).not.toHaveBeenCalled()
    })
  })
})
