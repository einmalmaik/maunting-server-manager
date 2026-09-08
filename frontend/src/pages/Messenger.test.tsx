import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { Messenger } from './Messenger'
import * as socialApi from '@/api/social'
import { teamsApi } from '@/api/teams'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/api/social', () => ({
  getFriends: vi.fn(),
  getGroups: vi.fn().mockResolvedValue([]),
  createGroup: vi.fn(),
  deleteGroup: vi.fn(),
  joinGroupByInvite: vi.fn(),
  leaveGroup: vi.fn(),
  getStories: vi.fn().mockResolvedValue([]),
  createStory: vi.fn(),
  deleteStory: vi.fn(),
  getGroupMembers: vi.fn().mockResolvedValue([]),
  updateGroupMemberRole: vi.fn(),
  kickGroupMember: vi.fn(),
  updateGroupPermissions: vi.fn(),
  getE2eePublicKey: vi.fn(),
  setE2eePublicKey: vi.fn(),
  relayE2eeEnvelope: vi.fn(),
  fetchE2eeEnvelopes: vi.fn(),
  getPublicProfiles: vi.fn().mockResolvedValue([]),
  sendFriendRequest: vi.fn().mockResolvedValue({ success: true, message: 'Anfrage gesendet' }),
}))

vi.mock('@/api/teams', () => ({
  teamsApi: {
    list: vi.fn(),
    get: vi.fn(),
  },
}))

vi.mock('@/services/e2eeCrypto', () => ({
  deriveBlindMailboxId: vi.fn().mockResolvedValue('test-blind-mailbox'),
  deriveGroupBlindMailboxId: vi.fn().mockResolvedValue('test-group-blind-mailbox'),
  encryptE2eeMessage: vi.fn().mockResolvedValue('ciphertext'),
  decryptE2eeMessage: vi.fn().mockResolvedValue('Hallo Welt'),
  encryptE2eeHybrid: vi.fn().mockResolvedValue('sv-e2ee-hybrid-v1:...'),
  decryptE2eeHybrid: vi.fn().mockResolvedValue('Hallo Hybrid'),
  encryptGroupE2eeMessage: vi.fn().mockResolvedValue('group-ciphertext'),
  decryptGroupE2eeMessage: vi.fn().mockResolvedValue('Hallo Gruppe'),
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

    vi.mocked(socialApi.getGroups).mockResolvedValue([])
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
    // Open unified attachment menu
    fireEvent.click(screen.getByLabelText('Anhang hinzufügen'))
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

    const sendBtn = screen.getByTitle('Senden')
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          recipient_user_id: 202,
        })
      )
    })
  })

  it('rendert Gruppen, filtert nach Gruppen und öffnet Gruppenchat mit Verschlüsselungsanzeige', async () => {
    vi.mocked(socialApi.getGroups).mockResolvedValue([
      {
        id: 77,
        name: 'Dev Community',
        description: 'Offizielle Entwicklergruppe',
        avatar_url: null,
        invite_code: 'dev-invite-123',
        owner_user_id: 1,
        member_count: 5,
        role: 'admin',
        created_at: '2026-09-07T00:00:00Z',
        members: [],
      },
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Dev Community')).toBeInTheDocument()
      expect(screen.getByText('Offizielle Entwicklergruppe')).toBeInTheDocument()
      expect(screen.getByText('5 M.')).toBeInTheDocument()
    })

    // Click on group to open group chat
    fireEvent.click(screen.getByText('Dev Community'))

    await waitFor(() => {
      expect(screen.getByText('Gruppen-E2EE verschlüsselt')).toBeInTheDocument()
      expect(screen.getByText('5 Mitglieder')).toBeInTheDocument()
      expect(screen.getByText('Einladen')).toBeInTheDocument()
    })
  })

  it('erlaubt das Erstellen einer neuen Gruppe über den Dialog', async () => {
    vi.mocked(socialApi.createGroup).mockResolvedValueOnce({
      id: 88,
      name: 'Neue Supergruppe',
      description: 'Testbeschreibung',
      avatar_url: null,
      invite_code: 'super-invite-code',
      owner_user_id: 1,
      member_count: 1,
      role: 'admin',
      created_at: '2026-09-07T00:00:00Z',
      members: [],
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    // Open create group dialog
    const createBtn = screen.getAllByLabelText('Neue Gruppe erstellen')[0]
    fireEvent.click(createBtn)

    await waitFor(() => {
      expect(screen.getByText('Neue Gruppe erstellen')).toBeInTheDocument()
    })

    const nameInput = screen.getByPlaceholderText('z. B. Server-Admins oder Gaming')
    fireEvent.change(nameInput, { target: { value: 'Neue Supergruppe' } })

    const submitBtn = screen.getByRole('button', { name: 'Gruppe erstellen' })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(socialApi.createGroup).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Neue Supergruppe',
        })
      )
    })
  })

  it('zeigt WhatsApp-typischen Sprachnachricht-Button bei leerem Textfeld und Senden-Button bei Eingabe', async () => {
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
      expect(screen.getByLabelText('Sprachnachricht aufnehmen')).toBeInTheDocument()
    })

    // Typing text replaces Mic button with Send button
    const input = screen.getByPlaceholderText('Nachricht schreiben …')
    fireEvent.change(input, { target: { value: 'Hey!' } })

    expect(screen.queryByLabelText('Sprachnachricht aufnehmen')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Senden/i })).toBeInTheDocument()

    // Clearing input restores Mic button
    fireEvent.change(input, { target: { value: '' } })
    expect(screen.getByLabelText('Sprachnachricht aufnehmen')).toBeInTheDocument()
  })

  it('öffnet den Sticker- und Emoji-Wähler im Chat', async () => {
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
      expect(screen.getByLabelText('Sticker auswählen')).toBeInTheDocument()
    })

    // Click sticker toggle button
    fireEvent.click(screen.getByLabelText('Sticker auswählen'))

    // Expect sticker and emoji tab buttons
    expect(screen.getByRole('button', { name: 'Sticker' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Emojis' })).toBeInTheDocument()

    // Switch to Emojis tab
    fireEvent.click(screen.getByRole('button', { name: 'Emojis' }))
    expect(screen.getByText('👍')).toBeInTheDocument()

    // Switch back to Stickers tab
    fireEvent.click(screen.getByRole('button', { name: 'Sticker' }))
    expect(screen.getByText('Feuer')).toBeInTheDocument()
  })

  it('schaltet zwischen den WhatsApp-typischen Reitern auf Mobilgeräten um', async () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    // Click Aktuelles tab
    const updatesTab = screen.getByRole('button', { name: 'Aktuelles' })
    fireEvent.click(updatesTab)
    expect(screen.getByText('Status')).toBeInTheDocument()

    // Click Community tab
    const communityTab = screen.getByRole('button', { name: 'Community' })
    fireEvent.click(communityTab)
    expect(screen.getByText('Communities & Gruppen')).toBeInTheDocument()

    // Audio tab should be removed
    expect(screen.queryByRole('button', { name: 'Audio' })).not.toBeInTheDocument()

    // Switch back to Chats tab
    const chatsTab = screen.getByRole('button', { name: 'Chats' })
    fireEvent.click(chatsTab)
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('bietet eine Schnellkamera-Schaltfläche in der Kopfzeile an', () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    const cameraBtn = screen.getByRole('button', { name: 'Foto aufnehmen' })
    expect(cameraBtn).toBeInTheDocument()
  })

  it('erlaubt dem Gruppen-Eigentümer das Löschen der Gruppe', async () => {
    vi.mocked(socialApi.getGroups).mockResolvedValue([
      {
        id: 77,
        name: 'Delete Me Clan',
        description: 'Temporary group',
        invite_code: 'temp-123',
        owner_user_id: 1,
        member_count: 3,
        role: 'admin',
        created_at: '2026-09-02T00:00:00Z',
        members: [],
      } as any,
    ])
    vi.mocked(socialApi.deleteGroup).mockResolvedValueOnce({ success: true } as any)

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Delete Me Clan')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Delete Me Clan'))

    await waitFor(() => {
      expect(screen.getByLabelText('Gruppe löschen')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText('Gruppe löschen'))

    await waitFor(() => {
      expect(screen.getByText('Endgültig löschen')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Endgültig löschen'))

    await waitFor(() => {
      expect(socialApi.deleteGroup).toHaveBeenCalledWith(77)
    })
  })

  it('öffnet Gruppenrollen & Rechte Modal über den Shield-Button im Header', async () => {
    vi.mocked(socialApi.getGroups).mockResolvedValue([
      {
        id: 88,
        name: 'Admin Tribe',
        description: 'Protected group',
        invite_code: 'admin-123',
        owner_user_id: 1,
        member_count: 2,
        role: 'owner',
        created_at: '2026-09-02T00:00:00Z',
        members: [],
      } as any,
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Admin Tribe')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Admin Tribe'))

    await waitFor(() => {
      expect(screen.getByLabelText('Gruppenrollen & Rechte verwalten')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText('Gruppenrollen & Rechte verwalten'))

    await waitFor(() => {
      expect(screen.getByText('Gruppen-Rollen & Rechte')).toBeInTheDocument()
    })
  })

  it('rendert Status-Stories unter Aktuelles und öffnet den Erstellungs-Dialog', async () => {
    vi.mocked(socialApi.getStories).mockResolvedValue([
      {
        id: 1,
        user_id: 101,
        username: 'alice',
        user_avatar: null,
        content: 'Mein cooler Status',
        media_url: null,
        background: 'gradient-1',
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 86400000).toISOString(),
      },
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getAllByText('alice')[0]).toBeInTheDocument()
    })

    const updatesTab = screen.getByRole('button', { name: 'Aktuelles' })
    fireEvent.click(updatesTab)

    await waitFor(() => {
      expect(screen.getAllByText('Mein Status')[0]).toBeInTheDocument()
      expect(screen.getByText('Hinzufügen')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Hinzufügen'))

    await waitFor(() => {
      expect(screen.getByText('Status erstellen')).toBeInTheDocument()
    })
  })

  it('entdeckt öffentliche Profile im Messenger und erlaubt Direktchats sowie Freundschaftsanfragen', async () => {
    vi.mocked(socialApi.getPublicProfiles).mockResolvedValue([
      {
        user_id: 303,
        username: 'bob_public',
        social_privacy: 'public',
        is_friend: false,
        presence: {
          user_id: 303,
          username: 'bob_public',
          status: 'online',
          device_type: 'desktop',
          activity_label: 'Online',
          last_seen_at: null,
          updated_at: null,
        },
      },
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('bob_public')).toBeInTheDocument()
    })

    // Filter by public tab
    const publicTab = screen.getByTitle(/Öffentlich/i)
    fireEvent.click(publicTab)

    expect(screen.getByRole('button', { name: /bob_public/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /alice/i })).not.toBeInTheDocument()

    // Click on public contact to open chat
    fireEvent.click(screen.getByRole('button', { name: /bob_public/i }))

    await waitFor(() => {
      expect(screen.getByText('Anfrage senden')).toBeInTheDocument()
    })

    // Send friend request
    fireEvent.click(screen.getByText('Anfrage senden'))
    expect(socialApi.sendFriendRequest).toHaveBeenCalledWith('bob_public')
  })
})
