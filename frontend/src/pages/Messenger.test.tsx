import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { Messenger } from './Messenger'
import * as socialApi from '@/api/social'
import { teamsApi } from '@/api/teams'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/social', () => ({
  getFriends: vi.fn(),
  getE2eePublicKey: vi.fn(),
  setE2eePublicKey: vi.fn(),
  relayE2eeEnvelope: vi.fn(),
  fetchE2eeEnvelopes: vi.fn(),
}))

vi.mock('@/api/teams', () => ({
  teamsApi: {
    list: vi.fn(),
    get: vi.fn(),
  },
}))

vi.mock('@/services/e2eeCrypto', () => ({
  deriveBlindMailboxId: vi.fn().mockResolvedValue('test-blind-mailbox'),
  encryptE2eeMessage: vi.fn().mockResolvedValue('ciphertext'),
  decryptE2eeMessage: vi.fn().mockResolvedValue('Hallo Welt'),
  encryptE2eeHybrid: vi.fn().mockResolvedValue('sv-e2ee-hybrid-v1:...'),
  decryptE2eeHybrid: vi.fn().mockResolvedValue('Hallo Hybrid'),
  getOrGenerateLocalKeyPair: vi.fn().mockResolvedValue({
    publicKeyJwk: '{"kty":"oct"}',
    privateKeyJwk: '{"kty":"oct"}',
  }),
}))

vi.mock('@/lib/offlineSync', () => ({
  loadNotesOfflineFirst: vi.fn().mockResolvedValue({ notes: [] }),
  loadCalendarEventsOfflineFirst: vi.fn().mockResolvedValue({ events: [] }),
}))

function setupUser() {
  useAuthStore.setState({
    user: {
      id: 1,
      username: 'me',
      email: 'me@example.test',
      is_owner: false,
      is_active: true,
      email_verified: true,
      two_factor_enabled: false,
      email_notifications: false,
      ai_notifications: false,
      device_notifications: false,
      role_id: null,
      created_at: '2026-08-28T00:00:00Z',
    },
    isAuthenticated: true,
  })
}

describe('Messenger (Allround Chat)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setupUser()

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
          device_type: 'web',
          activity_label: 'Im Panel',
          activity_detail: null,
        },
      },
    ])

    vi.mocked(teamsApi.list).mockResolvedValue([
      { id: 'team-1', name: 'Dev Team', description: '', created_at: '' },
    ])

    vi.mocked(teamsApi.get).mockResolvedValue({
      id: 'team-1',
      name: 'Dev Team',
      description: '',
      created_at: '',
      members: [
        {
          user_id: 1,
          username: 'me',
          email: 'me@example.test',
          role: 'member',
          joined_at: '',
        },
        {
          user_id: 202,
          username: 'charlie_teammate',
          email: 'charlie@example.test',
          role: 'member',
          joined_at: '',
        },
      ],
    } as any)

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([])
    vi.mocked(socialApi.getE2eePublicKey).mockResolvedValue({ user_id: 101, username: 'alice', public_key: null })
  })

  it('rendert Kontakte inklusive Freunde und Teammitglieder ohne bestehenden Freundschaftsstatus', async () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
      expect(screen.getByText('charlie_teammate')).toBeInTheDocument()
    })

    expect(screen.getByText('Dev Team')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Freunde oder Teammitglieder suchen …')).toBeInTheDocument()
  })

  it('öffnet die Konversation beim Klick auf einen Kontakt und zeigt dezente E2EE-Statuszeile', async () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('alice'))

    await waitFor(() => {
      expect(screen.getByText('Nachrichten in diesem Chat sind Ende-zu-Ende verschlüsselt.')).toBeInTheDocument()
    })

    expect(screen.getByPlaceholderText('Nachricht schreiben …')).toBeInTheDocument()
    expect(screen.getByLabelText('Foto anhängen')).toBeInTheDocument()
    expect(screen.getByLabelText('Notiz teilen')).toBeInTheDocument()
    expect(screen.getByLabelText('Kalendereintrag teilen')).toBeInTheDocument()
  })

  it('erlaubt das Senden einer Nachricht an ein Teammitglied ohne vorherige Freundschaft', async () => {
    vi.mocked(socialApi.relayE2eeEnvelope).mockResolvedValueOnce({
      id: 999,
      blind_mailbox_id: 'test-blind-mailbox',
      ciphertext_envelope: 'ciphertext',
      created_at: new Date().toISOString(),
    })

    render(
      <MemoryRouter initialEntries={['/chat?userId=202']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Nachricht schreiben …')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Nachricht schreiben …')
    fireEvent.change(input, { target: { value: 'Hallo Teammate!' } })

    const sendBtn = screen.getByRole('button', { name: /Senden/i })
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          recipient_user_id: 202,
        })
      )
    })
  })
})
