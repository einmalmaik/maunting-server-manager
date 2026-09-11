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
  uploadEncryptedChatAttachment: vi.fn(),
  getChatMediaSignedUrl: vi.fn(),
  downloadAndDecryptChatAttachment: vi.fn(),
}))

vi.mock('@/api/teams', () => ({
  teamsApi: {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn().mockResolvedValue(null),
  },
}))

vi.mock('@/services/e2eeCrypto', () => ({
  deriveBlindMailboxId: vi.fn().mockResolvedValue('test-blind-mailbox'),
  deriveGroupBlindMailboxId: vi.fn().mockResolvedValue('test-group-blind-mailbox'),
  getCachedBlindMailboxId: vi.fn().mockReturnValue('test-blind-mailbox'),
  getCachedGroupBlindMailboxId: vi.fn().mockReturnValue('test-group-blind-mailbox'),
  envelopePlaintextCache: new Map(),
  clearEnvelopePlaintextCache: vi.fn(),
  encryptE2eeMessage: vi.fn().mockResolvedValue('ciphertext'),
  decryptE2eeMessage: vi.fn().mockResolvedValue('Hallo Welt'),
  encryptE2eeHybrid: vi.fn().mockImplementation(async (payload) => `sv-e2ee-hybrid-v1:mock.${payload}`),
  decryptE2eeHybrid: vi.fn().mockImplementation(async (envelope) => {
    return envelope.replace('sv-e2ee-hybrid-v1:mock.', '')
  }),
  encryptGroupE2eeMessage: vi.fn().mockResolvedValue('group-ciphertext'),
  decryptGroupE2eeMessage: vi.fn().mockResolvedValue('Hallo Gruppe'),
  getOrGenerateLocalKeyPair: vi.fn().mockResolvedValue({
    publicKeyJwk: '{"kty":"oct"}',
    privateKeyJwk: '{"kty":"oct"}',
  }),
  scrubPlaintextStorage: vi.fn(),
}))

vi.mock('@/lib/offlineSync', () => ({
  loadNotesOfflineFirst: vi.fn().mockResolvedValue({ notes: [] }),
  loadCalendarEventsOfflineFirst: vi.fn().mockResolvedValue({ events: [] }),
  saveNoteOffline: vi.fn().mockResolvedValue({ id: 1, title: 'Mock' }),
  saveCalendarEventOffline: vi.fn().mockResolvedValue({ id: 1, title: 'Mock' }),
  enqueueMessageMutation: vi.fn().mockReturnValue({ id: 'mock-mutation' }),
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

describe('Messenger Attachment Flow', () => {
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

    vi.mocked(socialApi.getGroups).mockResolvedValue([])
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([])
    vi.mocked(socialApi.getE2eePublicKey).mockResolvedValue({ user_id: 101, username: 'alice', public_key: 'mock-pub-key' })
    vi.mocked(socialApi.uploadEncryptedChatAttachment).mockResolvedValue({
      id: 'mock-media-123',
      blind_mailbox_id: 'test-blind-mailbox',
      file_name: 'test.png',
      media_type: 'image/png',
      size_bytes: 100,
      sha256: 'mock-hash',
      created_at: new Date().toISOString(),
    })
  })

  it('renders received image attachment and file attachment in the message list', async () => {
    const mockImagePayload = JSON.stringify({
      client_uuid: 'uuid-1',
      sender_id: 101,
      sender_name: 'alice',
      text: '',
      timestamp: new Date().toISOString(),
      image_attachment: {
        dataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
        name: 'test.png',
        mediaId: 'mock-media-123',
      },
    })

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 1,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: `sv-e2ee-hybrid-v1:mock.${mockImagePayload}`,
        created_at: new Date().toISOString(),
      },
    ])

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    await waitFor(() => {
      const img = screen.queryByAltText('test.png') || screen.queryByAltText('Chat Anhang')
      expect(img).toBeInTheDocument()
    })
  })

  it('uploads an image and verifies if the message is displayed for sender', async () => {
    let storedEnvelope: any = null

    vi.mocked(socialApi.relayE2eeEnvelope).mockImplementation(async (payload) => {
      storedEnvelope = {
        id: 1002,
        blind_mailbox_id: payload.blind_mailbox_id,
        ciphertext_envelope: payload.ciphertext_envelope,
        client_uuid: payload.client_uuid,
        created_at: new Date().toISOString(),
      }
      return storedEnvelope
    })

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockImplementation(async () => {
      return storedEnvelope ? [storedEnvelope] : []
    })

    const { container } = render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Nachricht schreiben …')).toBeInTheDocument()
    })

    const fileInput = container.querySelector('input[type="file"][accept="image/*"]') as HTMLInputElement
    expect(fileInput).toBeInTheDocument()

    const file = new File(['fake-png-content'], 'test.png', { type: 'image/png' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    // Staged preview bar should appear
    await waitFor(() => {
      expect(screen.getByText(/Foto angehängt/)).toBeInTheDocument()
    })

    // Click send
    const sendButton = screen.getByTitle('Senden')
    fireEvent.click(sendButton)

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalled()
    })

    await waitFor(() => {
      const chatMessages = container.querySelectorAll('.group.flex.flex-col')
      expect(chatMessages.length).toBeGreaterThan(0)
    })
  })

  it('downloads and decrypts an image attachment that only has mediaId', async () => {
    vi.mocked(socialApi.getChatMediaSignedUrl).mockResolvedValue({
      media_id: 'mock-remote-media-456',
      signed_url: 'https://example.test/media/signed.bin',
      expires_at: new Date(Date.now() + 60000).toISOString(),
    })
    vi.mocked(socialApi.downloadAndDecryptChatAttachment).mockResolvedValue(
      'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    )

    const mockPayload = JSON.stringify({
      client_uuid: 'uuid-lazy-image',
      sender_id: 101,
      sender_name: 'alice',
      text: '',
      timestamp: new Date().toISOString(),
      image_attachment: {
        mediaId: 'mock-remote-media-456',
        name: 'remote-foto.png',
      },
    })

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 201,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: `sv-e2ee-hybrid-v1:mock.${mockPayload}`,
        created_at: new Date().toISOString(),
      },
    ])

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(socialApi.getChatMediaSignedUrl).toHaveBeenCalledWith('mock-remote-media-456')
      expect(socialApi.downloadAndDecryptChatAttachment).toHaveBeenCalledWith(
        'https://example.test/media/signed.bin',
        expect.anything()
      )
    })

    await waitFor(() => {
      const img = screen.queryByAltText('remote-foto.png') || screen.queryByAltText('Chat Anhang')
      expect(img).toBeInTheDocument()
    })
  })

  it('renders a file attachment and decrypts on click if only mediaId is present', async () => {
    vi.mocked(socialApi.getChatMediaSignedUrl).mockResolvedValue({
      media_id: 'mock-file-media-789',
      signed_url: 'https://example.test/media/signed-file.bin',
      expires_at: new Date(Date.now() + 60000).toISOString(),
    })
    vi.mocked(socialApi.downloadAndDecryptChatAttachment).mockResolvedValue(
      'data:application/pdf;base64,JVBERi0xLjQKJcTl8uXr...'
    )

    const mockPayload = JSON.stringify({
      client_uuid: 'uuid-file-attachment',
      sender_id: 101,
      sender_name: 'alice',
      text: 'Hier ist das Dokument',
      timestamp: new Date().toISOString(),
      file_attachment: {
        mediaId: 'mock-file-media-789',
        name: 'dokument.pdf',
        sizeBytes: 2048,
      },
    })

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 301,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: `sv-e2ee-hybrid-v1:mock.${mockPayload}`,
        created_at: new Date().toISOString(),
      },
    ])

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('dokument.pdf')).toBeInTheDocument()
      expect(screen.getByText('Hier ist das Dokument')).toBeInTheDocument()
    })

    const fileCard = screen.getByText('dokument.pdf').closest('a')
    expect(fileCard).toBeInTheDocument()
    if (fileCard) {
      fireEvent.click(fileCard)
    }

    await waitFor(() => {
      expect(socialApi.getChatMediaSignedUrl).toHaveBeenCalledWith('mock-file-media-789')
      expect(socialApi.downloadAndDecryptChatAttachment).toHaveBeenCalledWith(
        'https://example.test/media/signed-file.bin',
        expect.anything()
      )
    })
  })
})


