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
  getDirectChats: vi.fn().mockResolvedValue([]),
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
  sendTypingSignal: vi.fn().mockResolvedValue({ ok: true }),
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
  saveNoteOffline: vi.fn().mockResolvedValue({ id: 1, title: 'Mock' }),
  saveCalendarEventOffline: vi.fn().mockResolvedValue({ id: 1, title: 'Mock' }),
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
        expect.not.objectContaining({
          recipient_user_id: expect.anything(),
        })
      )
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          blind_mailbox_id: expect.any(String),
          ciphertext_envelope: expect.any(String),
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
      expect(screen.getByText('Einladen')).toBeInTheDocument()
      expect(screen.getByPlaceholderText('Nachricht schreiben …')).toBeInTheDocument()
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

  it('unterstützt dynamische Lesebestätigungen und das Bearbeiten & Löschen von Nachrichten (Zero Knowledge)', async () => {
    // 1. Setup existing chat envelopes (E2EE)
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 10,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-10',
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 11,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-11',
        created_at: '2026-09-08T12:01:00Z',
      },
    ])

    const { decryptE2eeMessage } = await import('@/services/e2eeCrypto')
    vi.mocked(decryptE2eeMessage).mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-10') {
        return JSON.stringify({
          sender_id: 1, // Self
          text: 'Meine ursprüngliche Nachricht',
          timestamp: '2026-09-08T12:00:00Z',
        })
      }
      if (envelope === 'ciphertext-11') {
        return JSON.stringify({
          sender_id: 101, // Alice
          text: 'Hallo von Alice!',
          timestamp: '2026-09-08T12:01:00Z',
        })
      }
      return 'Unbekannt'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    // Open chat with Alice
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /alice/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /alice/i }))

    await waitFor(() => {
      expect(screen.getByText('Meine ursprüngliche Nachricht')).toBeInTheDocument()
      expect(screen.getByText('Hallo von Alice!')).toBeInTheDocument()
      // Initial status before acknowledgement is "Gesendet" or "Zugestellt"
      expect(screen.getByTitle(/Gesendet|Zugestellt/)).toBeInTheDocument()
    })

    // 2. Simulate incoming read receipt envelope from Alice for message 10
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 10,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-10',
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 11,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-11',
        created_at: '2026-09-08T12:01:00Z',
      },
      {
        id: 12,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-read-receipt',
        created_at: '2026-09-08T12:02:00Z',
      },
    ])
    vi.mocked(decryptE2eeMessage).mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-10') {
        return JSON.stringify({
          sender_id: 1,
          text: 'Meine ursprüngliche Nachricht',
          timestamp: '2026-09-08T12:00:00Z',
        })
      }
      if (envelope === 'ciphertext-11') {
        return JSON.stringify({
          sender_id: 101,
          text: 'Hallo von Alice!',
          timestamp: '2026-09-08T12:01:00Z',
        })
      }
      if (envelope === 'ciphertext-read-receipt') {
        return JSON.stringify({
          type: 'read_receipt',
          read_up_to_id: 10,
          reader_id: 101, // Alice read our message
        })
      }
      return 'Unbekannt'
    })

    // Trigger sync event
    window.dispatchEvent(
      new CustomEvent('msm:sync-event', {
        detail: { type: 'e2ee_blind_message', blind_mailbox_id: 'test-blind-mailbox' },
      })
    )

    await waitFor(() => {
      expect(screen.getByTitle('Gelesen vom Gesprächspartner')).toBeInTheDocument()
    })

    // 3. Test Editing Message
    const editBtn = screen.getByTitle('Nachricht bearbeiten')
    fireEvent.click(editBtn)

    expect(screen.getByText('Nachricht bearbeiten')).toBeInTheDocument()
    const input = screen.getByPlaceholderText('Nachricht bearbeiten …')
    fireEvent.change(input, { target: { value: 'Meine korrigierte Nachricht' } })

    const sendBtn = screen.getByTitle('Senden')
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          blind_mailbox_id: 'test-blind-mailbox',
        })
      )
    })

    // 4. Test Deleting Message with Opferschutz / Beweissicherung
    const deleteBtn = screen.getByTitle('Nachricht für alle löschen')
    fireEvent.click(deleteBtn)

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          blind_mailbox_id: 'test-blind-mailbox',
        })
      )
    })
  })

  it('unterstützt WhatsApp-ähnliche Audiogeschwindigkeit und verhindert doppelte Kalender- & Notizeinträge', async () => {
    const { saveNoteOffline, saveCalendarEventOffline } = await import('@/lib/offlineSync')

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 20,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-voice',
        created_at: '2026-09-08T14:00:00Z',
      },
      {
        id: 21,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-note',
        created_at: '2026-09-08T14:01:00Z',
      },
      {
        id: 22,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-calendar',
        created_at: '2026-09-08T14:02:00Z',
      },
    ])

    const { decryptE2eeMessage } = await import('@/services/e2eeCrypto')
    vi.mocked(decryptE2eeMessage).mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-voice') {
        return JSON.stringify({
          sender_id: 101,
          audio_attachment: {
            dataUrl: 'data:audio/webm;base64,GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQRChYECGFOAZwE=',
            durationSeconds: 15,
            mimeType: 'audio/webm',
          },
        })
      }
      if (envelope === 'ciphertext-note') {
        return JSON.stringify({
          sender_id: 101,
          note_attachment: {
            title: 'Wichtige Notiz',
            content: 'Notizinhalt für den Test',
          },
        })
      }
      if (envelope === 'ciphertext-calendar') {
        return JSON.stringify({
          sender_id: 101,
          calendar_attachment: {
            title: 'Strategiemeeting',
            start: '2026-09-10T10:00:00Z',
            end: '2026-09-10T11:00:00Z',
            description: 'Vorbereitung',
          },
        })
      }
      return '{}'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /alice/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /alice/i }))

    // 1. Audio Playback Speed Toggle
    await waitFor(() => {
      expect(screen.getByText('1x')).toBeInTheDocument()
    })

    const speedBtn = screen.getByText('1x')
    fireEvent.click(speedBtn)
    expect(screen.getByText('1.5x')).toBeInTheDocument()

    fireEvent.click(screen.getByText('1.5x'))
    expect(screen.getByText('2x')).toBeInTheDocument()

    fireEvent.click(screen.getByText('2x'))
    expect(screen.getByText('1x')).toBeInTheDocument()

    // 2. Note Import (Double-click prevention)
    const importNoteBtn = screen.getByTitle('In eigene Notizen übernehmen')
    fireEvent.click(importNoteBtn)

    await waitFor(() => {
      expect(saveNoteOffline).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Übernommen')).toBeInTheDocument()
    })

    // Clicking again should not trigger saveNoteOffline again
    const importedNoteBtn = screen.getByTitle('Bereits in eigene Notizen übernommen')
    expect(importedNoteBtn).toBeDisabled()
    fireEvent.click(importedNoteBtn)
    expect(saveNoteOffline).toHaveBeenCalledTimes(1)

    // 3. Calendar Import (Double-click prevention)
    const importCalBtn = screen.getByTitle('In eigenen Kalender eintragen')
    fireEvent.click(importCalBtn)

    await waitFor(() => {
      expect(saveCalendarEventOffline).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Eingetragen')).toBeInTheDocument()
    })

    const importedCalBtn = screen.getByTitle('Bereits in eigenen Kalender eingetragen')
    expect(importedCalBtn).toBeDisabled()
    fireEvent.click(importedCalBtn)
    expect(saveCalendarEventOffline).toHaveBeenCalledTimes(1)
  })

  it('rendert Datumstrenner zwischen Nachrichten unterschiedlicher Tage', async () => {
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        friend_user_id: 102,
        username: 'bob',
        avatar_url: null,
        presence: { status: 'online' },
      } as any,
    ])

    const todayIso = new Date().toISOString()
    const yesterday = new Date()
    yesterday.setDate(yesterday.getDate() - 1)
    const yesterdayIso = yesterday.toISOString()

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 1,
        blind_mailbox_id: 'mailbox-102',
        ciphertext_envelope: 'ciphertext-yesterday',
        created_at: yesterdayIso,
      },
      {
        id: 2,
        blind_mailbox_id: 'mailbox-102',
        ciphertext_envelope: 'ciphertext-today',
        created_at: todayIso,
      },
    ])

    const { decryptE2eeMessage } = await import('@/services/e2eeCrypto')
    vi.mocked(decryptE2eeMessage).mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-yesterday') {
        return JSON.stringify({
          sender_id: 102,
          text: 'Hallo von gestern!',
        })
      }
      if (envelope === 'ciphertext-today') {
        return JSON.stringify({
          sender_id: 102,
          text: 'Hallo von heute!',
        })
      }
      return '{}'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /bob/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /bob/i }))

    await waitFor(() => {
      expect(screen.getByText('Hallo von gestern!')).toBeInTheDocument()
      expect(screen.getByText('Hallo von heute!')).toBeInTheDocument()
    })

    // Both date badges should be present
    expect(screen.getByText('Gestern')).toBeInTheDocument()
    expect(screen.getByText('Heute')).toBeInTheDocument()
  })

  it('öffnet das Chat-Hintergrund-Modal und erlaubt die Auswahl von Presets', async () => {
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        friend_user_id: 103,
        username: 'charlie',
        avatar_url: null,
        presence: { status: 'online' },
      } as any,
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /charlie/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /charlie/i }))

    // Wallpaper button in chat header
    await waitFor(() => {
      expect(screen.getByLabelText('Chat-Hintergrund anpassen')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText('Chat-Hintergrund anpassen'))

    // Modal opens
    await waitFor(() => {
      expect(screen.getByText('Chat-Hintergrund anpassen')).toBeInTheDocument()
      expect(screen.getByText('Cyber Grid')).toBeInTheDocument()
      expect(screen.getByText('Deep Petrol')).toBeInTheDocument()
      expect(screen.getByText('Mitternacht')).toBeInTheDocument()
      expect(screen.getByText('Schlicht Dunkel')).toBeInTheDocument()
    })

    // Select Midnight preset and apply
    fireEvent.click(screen.getByText('Mitternacht'))
    fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }))

    await waitFor(() => {
      expect(screen.queryByText('Design-Hintergründe')).not.toBeInTheDocument()
    })
  })

  it('rendert Story-Antworten mit reichhaltiger Vorschau und sendet Typing-Signale beim Tippen', async () => {
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 104,
        user_id: 104,
        friend_user_id: 104,
        username: 'diana',
        status: 'accepted',
        is_requester: false,
        avatar_url: null,
        presence: { status: 'online' },
      } as any,
    ])

    const nowIso = new Date().toISOString()
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 1,
        blind_mailbox_id: 'mailbox-104',
        ciphertext_envelope: 'ciphertext-story-reply',
        created_at: nowIso,
      },
    ])

    const { decryptE2eeMessage } = await import('@/services/e2eeCrypto')
    vi.mocked(decryptE2eeMessage).mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-story-reply') {
        return JSON.stringify({
          sender_id: 104,
          text: 'Tolles Bild!',
          story_reply: {
            storyId: 42,
            storyContent: 'Urlaubsausblick 2026',
            storyUsername: 'me',
          },
        })
      }
      return '{}'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /diana/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /diana/i }))

    // Story reply preview card should render
    await waitFor(() => {
      expect(screen.getByText('Status von me')).toBeInTheDocument()
      expect(screen.getByText('Urlaubsausblick 2026')).toBeInTheDocument()
      expect(screen.getByText('Tolles Bild!')).toBeInTheDocument()
    })

    // Typing sends typing signal
    const input = screen.getByPlaceholderText('Nachricht schreiben …')
    fireEvent.change(input, { target: { value: 'Ich schreibe gerade' } })

    await waitFor(() => {
      expect(socialApi.sendTypingSignal).toHaveBeenCalledWith(
        expect.objectContaining({
          status: 'typing',
          recipient_id: 104,
        })
      )
    })
  })

  it('synchronisiert Nachrichten plattformuebergreifend (Tauri <-> Web) per deterministischer Kanalverschluesselung', async () => {
    const { encryptE2eeMessage } = await import('@/services/e2eeCrypto')
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 99, // Friendship table ID
        user_id: 205, // Actual user ID
        username: 'bob_desktop',
        avatar_url: null,
        status: 'accepted',
        presence: { status: 'online', device_type: 'desktop' },
      },
    ])
    // Both users have registered public keys (e.g. from Tauri or Web)
    vi.mocked(socialApi.getE2eePublicKey).mockResolvedValue({
      user_id: 205,
      username: 'bob_desktop',
      public_key: '{"kty":"RSA","n":"pub_bob"}',
    })
    vi.mocked(encryptE2eeMessage).mockResolvedValue('sv-e2ee-v1:cross-platform-sync-envelope')

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /bob_desktop/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /bob_desktop/i }))

    await waitFor(() => {
      expect(screen.getByText('Nachrichten in diesem Chat sind Ende-zu-Ende verschlüsselt.')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText('Nachricht schreiben …')
    fireEvent.change(input, { target: { value: 'Nachricht aus Tauri' } })

    // Typing signal uses actual userId (205), not friendship id (99)
    await waitFor(() => {
      expect(socialApi.sendTypingSignal).toHaveBeenCalledWith(
        expect.objectContaining({
          status: 'typing',
          recipient_id: 205,
        })
      )
    })

    const sendBtn = screen.getByTitle('Senden')
    fireEvent.click(sendBtn)

    // Message is encrypted via deterministic channel key and relayed with recipient_id = 205
    await waitFor(() => {
      expect(encryptE2eeMessage).toHaveBeenCalledWith(
        expect.stringContaining('Nachricht aus Tauri'),
        1,
        205
      )
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          recipient_id: 205,
          ciphertext_envelope: 'sv-e2ee-v1:cross-platform-sync-envelope',
        })
      )
    })
  })

  it('entschluesselt empfangene Nachrichten aus Tauri/Web zuverlaessig ueber den synchronisierten Direktkanal', async () => {
    const { decryptE2eeMessage, decryptE2eeHybrid } = await import('@/services/e2eeCrypto')
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        user_id: 206,
        username: 'charlie_e2ee',
        avatar_url: null,
        status: 'accepted',
        presence: { status: 'online' },
      },
    ])
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 501,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'sv-e2ee-v1:message-from-tauri',
        created_at: '2026-09-10T12:00:00Z',
      },
      {
        id: 502,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'sv-e2ee-v1:message-from-web',
        created_at: '2026-09-10T12:01:00Z',
      },
      {
        id: 503,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'sv-e2ee-hybrid-v1:unmatched-device-key',
        created_at: '2026-09-10T12:02:00Z',
      },
    ])

    // Direct channel decryption succeeds for cross-platform messages
    vi.mocked(decryptE2eeMessage).mockImplementation(async (env) => {
      if (env.includes('message-from-tauri')) {
        return JSON.stringify({
          sender_id: 206,
          text: 'Nachricht aus Tauri auf Web lesbar',
          timestamp: '2026-09-10T12:00:00Z',
        })
      }
      if (env.includes('message-from-web')) {
        return JSON.stringify({
          sender_id: 206,
          text: 'Nachricht aus Web auf Tauri lesbar',
          timestamp: '2026-09-10T12:01:00Z',
        })
      }
      return ''
    })

    // Unmatched legacy hybrid envelope throws
    vi.mocked(decryptE2eeHybrid).mockRejectedValue(new Error('Kein passender RSA-Schluessel'))

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /charlie_e2ee/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /charlie_e2ee/i }))

    // Cross-platform messages must be decoded and readable, and unmatched legacy hybrid safely shows encrypted notice
    await waitFor(() => {
      expect(screen.getByText('Nachricht aus Tauri auf Web lesbar')).toBeInTheDocument()
      expect(screen.getByText('Nachricht aus Web auf Tauri lesbar')).toBeInTheDocument()
      expect(screen.getByText('Verschlüsselte Nachricht')).toBeInTheDocument()
    })
  })
})

