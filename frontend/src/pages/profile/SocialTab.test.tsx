import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SocialTab } from './SocialTab'
import * as socialApi from '@/api/social'

vi.mock('@/api/social', () => ({
  getFriends: vi.fn(),
  getFriendRequests: vi.fn(),
  sendFriendRequest: vi.fn(),
  acceptFriendRequest: vi.fn(),
  declineFriendRequest: vi.fn(),
  removeFriend: vi.fn(),
  getAchievements: vi.fn(),
  getStats: vi.fn(),
}))

vi.mock('@/stores/toastStore', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

describe('SocialTab (Profile page)', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 101,
        user_id: 101,
        username: 'alice',
        avatar_url: null,
        status: 'accepted',
        is_requester: false,
        created_at: '2026-09-01T00:00:00Z',
        presence: {
          status: 'online',
          device_type: 'desktop',
          activity_label: 'Server-Administration',
          activity_detail: null,
        },
      },
    ])

    vi.mocked(socialApi.getFriendRequests).mockResolvedValue({
      incoming: [
        {
          id: 201,
          user_id: 201,
          username: 'bob',
          avatar_url: null,
          status: 'pending',
          is_requester: true,
          created_at: '2026-09-02T00:00:00Z',
          presence: null,
        },
      ],
      outgoing: [],
    })

    vi.mocked(socialApi.getAchievements).mockResolvedValue({
      total_unlocked: 1,
      total_available: 2,
      prestige_score: 25,
      achievements: [
        {
          id: 'starter_first_step',
          title: 'Erster Schritt',
          description: 'Erste erfolgreiche Anmeldung im MSM Control Panel.',
          category: 'starter',
          points: 10,
          icon: 'award',
          unlocked: true,
          unlocked_at: '2026-09-01T10:00:00Z',
          rarity_percent: 50.0,
          rarity_tier: 'common',
          rarity_text: 'Von 50% der Nutzer freigeschaltet',
        },
        {
          id: 'server_architect',
          title: 'Weltenbauer',
          description: 'Den ersten Spielserver erfolgreich aufgesetzt.',
          category: 'servers',
          points: 25,
          icon: 'server',
          unlocked: false,
          unlocked_at: null,
          rarity_percent: 5.0,
          rarity_tier: 'rare',
          rarity_text: 'Nur von 5% aller Nutzer freigeschaltet',
        },
      ],
    })

    vi.mocked(socialApi.getStats).mockResolvedValue({
      user_id: 1,
      active_time_seconds: 3600,
      total_activity_seconds: 3600,
      categories: {
        server_admin: 1800,
        ai_chat: 1800,
      },
      active_time_by_category: {
        server_admin: 1800,
        ai_chat: 1800,
      },
    })
  })

  it('rendert Freundesliste und Meilensteine ohne Gaming-Jargon', async () => {
    render(<SocialTab />)

    expect(screen.getByText('Freunde & Kontakte')).toBeInTheDocument()
    expect(screen.getByText('Meilensteine & Fortschritt')).toBeInTheDocument()

    // Confirmed friend
    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })
    expect(screen.getByText('Server-Administration')).toBeInTheDocument()

    // Incoming request
    expect(screen.getByText('bob')).toBeInTheDocument()
    expect(screen.getByText('Ausstehende Anfragen (1)')).toBeInTheDocument()

    // Milestones
    expect(screen.getByText('Erster Schritt')).toBeInTheDocument()
    expect(screen.getByText('Weltenbauer')).toBeInTheDocument()
    expect(screen.getByText('+10 Pkt')).toBeInTheDocument()
    expect(screen.getByText('+25 Pkt')).toBeInTheDocument()

    // No marketing or Steam prestige jargon
    expect(screen.queryByText(/Steam-Prestige/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/kompromisslose/i)).not.toBeInTheDocument()
  })

  it('ermöglicht das Annehmen einer Freundschaftsanfrage', async () => {
    vi.mocked(socialApi.acceptFriendRequest).mockResolvedValueOnce({
      success: true,
      message: 'Angenommen',
    })

    render(<SocialTab />)

    await waitFor(() => {
      expect(screen.getByText('bob')).toBeInTheDocument()
    })

    const acceptBtn = screen.getByRole('button', { name: /Annehmen/i })
    fireEvent.click(acceptBtn)

    await waitFor(() => {
      expect(socialApi.acceptFriendRequest).toHaveBeenCalledWith(201)
    })
  })

  it('filtert Meilensteine nach Status', async () => {
    render(<SocialTab />)

    await waitFor(() => {
      expect(screen.getByText('Erster Schritt')).toBeInTheDocument()
    })

    // Filter to locked only
    const lockedBtn = screen.getByRole('button', { name: /Gesperrt/i })
    fireEvent.click(lockedBtn)

    expect(screen.getByText('Weltenbauer')).toBeInTheDocument()
    expect(screen.queryByText('Erster Schritt')).not.toBeInTheDocument()

    // Filter to unlocked only
    const unlockedBtn = screen.getByRole('button', { name: /Freigeschaltet/i })
    fireEvent.click(unlockedBtn)

    expect(screen.getByText('Erster Schritt')).toBeInTheDocument()
    expect(screen.queryByText('Weltenbauer')).not.toBeInTheDocument()
  })

  it('validiert das Hinzufügen von Freunden (ignoriert Whitespace und akzeptiert Benutzernamen)', async () => {
    vi.mocked(socialApi.sendFriendRequest).mockResolvedValueOnce({
      success: true,
      message: 'Anfrage gesendet',
    })

    render(<SocialTab />)

    const input = screen.getByPlaceholderText('Benutzername eingeben …')
    const submitBtn = screen.getByRole('button', { name: /Anfrage senden/i })

    // Whitespace only
    fireEvent.change(input, { target: { value: '   ' } })
    expect(submitBtn).toBeDisabled()
    fireEvent.click(submitBtn)
    expect(socialApi.sendFriendRequest).not.toHaveBeenCalled()

    // Valid username with special characters
    fireEvent.change(input, { target: { value: 'charlie_01-test' } })
    expect(submitBtn).not.toBeDisabled()
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(socialApi.sendFriendRequest).toHaveBeenCalledWith('charlie_01-test')
    })
  })
})
