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
  getE2eeKeyring: vi.fn().mockResolvedValue({ wrapped_keyring: null, public_key: null, version: 0 }),
  putE2eeKeyring: vi.fn(),
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
  // Produktivpfad: gegen alle Schlüssel des Kontos, nicht gegen einen Gerätesschlüssel.
  decryptE2eeHybridWithKeyring: vi.fn().mockImplementation(async (envelope) => {
    return envelope.replace('sv-e2ee-hybrid-v1:mock.', '')
  }),
  encryptGroupE2eeMessage: vi.fn().mockResolvedValue('group-ciphertext'),
  decryptGroupE2eeMessage: vi.fn().mockResolvedValue('Hallo Gruppe'),
  scrubPlaintextStorage: vi.fn(),
}))

/** Zustand des Geräts direkt setzbar, statt je Test einen Bund zu öffnen. */
const { identitaet, MockRecipientKeyMissingError } = vi.hoisted(() => ({
  identitaet: {
    state: 'ready' as 'needs-setup' | 'locked' | 'ready',
    sendPair: { publicKeyJwk: '{"kty":"oct"}', privateKeyJwk: '{"kty":"oct"}' } as
      | { publicKeyJwk: string; privateKeyJwk: string }
      | null,
    decryptionKeys: ['{"kty":"oct"}'] as string[],
    empfaengerSchluessel: 'mock-empfaenger-pub-key' as string | null,
  },
  MockRecipientKeyMissingError: class extends Error {
    constructor(public readonly userId: number) {
      super('Für diesen Empfänger liegt kein Schlüssel vor')
      this.name = 'E2eeRecipientKeyMissingError'
    }
  },
}))

/**
 * Stellvertreter für den Ratchet-Pfad.
 *
 * Der echte Double Ratchet erzeugt je Zielgerät einen eigenen Umschlag und
 * verbraucht seinen Nachrichtenschlüssel beim Öffnen — beides braucht dieser
 * Test nicht nachzubilden, um Zustellung, Häkchen und Verlauf zu prüfen.
 * Nachgebildet wird genau das, was der Messenger von der Schicht sieht: das
 * Umschlagformat, das Auffächern in eine Liste und die Ablegefunktion, die vor
 * dem Fortschreiben läuft. Die Krypto selbst prüft `ratchetSitzung.test.ts`.
 */
/**
 * Der lokale Verlaufsspeicher im Arbeitsspeicher statt in IndexedDB.
 *
 * jsdom bringt keine IndexedDB mit, und der Messenger braucht sie seit dem
 * Ratchet zwingend: ein Nachrichtenschlüssel ist nach dem Öffnen verbraucht,
 * der Klartext muss also abgelegt sein, bevor der Zustand weiterrückt.
 * Nachgebildet wird hier nur die Ablage; dass die Reihenfolge stimmt, prüfen
 * `ratchetSpeicher.test.ts` und `ratchetSitzung.test.ts` gegen die echte.
 */
const { nachrichten, klartexte } = vi.hoisted(() => ({
  nachrichten: new Map<string, any[]>(),
  klartexte: new Map<string, Map<number, string>>(),
}))

/** Zwischen zwei Tests muss der Speicher leer sein, sonst leckt der Verlauf. */
function leereTestSpeicher(): void {
  nachrichten.clear()
  klartexte.clear()
}

vi.mock('@/services/messengerLocalStore', async () => {
  const echt = await vi.importActual<typeof import('@/services/messengerLocalStore')>(
    '@/services/messengerLocalStore'
  )
  return {
    ...echt,
    loadLocalMessages: vi.fn(async (mid: string) => nachrichten.get(mid) ?? []),
    saveLocalMessages: vi.fn(async (mid: string, msgs: any[]) => {
      nachrichten.set(mid, msgs)
    }),
    updateMessageInLocalStore: vi.fn(async () => {}),
    getLocalMailboxLastSyncedId: vi.fn(async () => 0),
    clearLocalMessengerStore: vi.fn(async () => {
      nachrichten.clear()
      klartexte.clear()
    }),
    speichereUmschlagKlartext: vi.fn(async (mid: string, id: number, plain: string) => {
      if (!klartexte.has(mid)) klartexte.set(mid, new Map())
      klartexte.get(mid)!.set(id, plain)
    }),
    ladeUmschlagKlartexte: vi.fn(
      async (mid: string) => new Map(klartexte.get(mid) ?? new Map())
    ),
  }
})

vi.mock('@/services/ratchetSitzung', () => {
  const PREFIX = 'sv-e2ee-dr-v1:'
  const einpacken = (t: string) => PREFIX + '1.testgeraet.zielgeraet.' + btoa(unescape(encodeURIComponent(t)))
  const auspacken = (u: string) => decodeURIComponent(escape(atob(u.split('.').slice(3).join('.'))))
  return {
    DR_PREFIX: PREFIX,
    DR_INIT_TYP: 'dr-init',
    baueZustellungen: vi.fn(async (_kontext: any, klartext: string, basisUuid: string) => [
      {
        empfaengerId: 101,
        zielGeraet: 'zielgeraet',
        bootstrap: null,
        nachricht: einpacken(klartext),
        clientUuid: basisUuid,
        bootstrapClientUuid: basisUuid + '#i',
      },
    ]),
    liesDrUmschlag: vi.fn(
      async (_kontext: any, umschlag: string, ablegen: (t: string) => Promise<void>) => {
        // Der Klartext kommt weiterhin aus dem Stellvertreter, den die Tests
        // ohnehin je Fall setzen. So bleibt jede bestehende Vorgabe gültig,
        // obwohl der Messenger jetzt über den Ratchet liest.
        const { decryptE2eeMessage } = await import('@/services/e2eeCrypto')
        let text: string
        try {
          text = umschlag.startsWith(PREFIX) && umschlag.split('.').length > 3
            ? auspacken(umschlag)
            : await (decryptE2eeMessage as any)(umschlag, 0, 0)
        } catch {
          return { art: 'bruch', vonKonto: 101, vonGeraet: 'zielgeraet', grund: 'Test' }
        }
        if (typeof text !== 'string' || text === '') {
          return { art: 'unbekannt' }
        }
        await ablegen(text)
        return { art: 'klartext', text, vonKonto: 101, vonGeraet: 'zielgeraet' }
      }
    ),
    verarbeiteBootstrap: vi.fn(async () => ({ istAufbau: false, ersetzt: false })),
    verwirfDrSitzung: vi.fn(async () => {}),
    logischeUuid: (u?: string | null) =>
      !u ? undefined : u.indexOf('#') === -1 ? u : u.slice(0, u.indexOf('#')),
  }
})

/**
 * Ein Geraet der Gegenstelle, mit genau dem Schluessel, den der Test gesetzt hat.
 *
 * Wichtig fuer die Dateien, die den echten Hybridumschlag benutzen: dort muss
 * hier ein gueltiger JWK stehen, sonst scheitert das Versiegeln der Quittungen
 * und der Test misst etwas anderes, als er glaubt.
 */
const zielGeraete = () => [
  {
    device_id: 'zielgeraet',
    public_key: identitaet.empfaengerSchluessel || 'mock-empfaenger-pub-key',
    label: '',
  },
]

vi.mock('@/services/e2eeGeraet', () => ({
  eigenesGeraet: vi.fn(async () => ({
    kennung: 'testgeraet',
    paar: identitaet.sendPair ?? { publicKeyJwk: '{"kty":"oct"}', privateKeyJwk: '{"kty":"oct"}' },
  })),
  geraeteVon: vi.fn(async () => zielGeraete()),
  verlangeGeraeteVon: vi.fn(async () => {
    if (!identitaet.empfaengerSchluessel) throw new MockRecipientKeyMissingError(0)
    return zielGeraete()
  }),
  vergessenGeraete: vi.fn(),
  clearGeraeteMemory: vi.fn(),
  E2eeKeinGeraetError: class extends Error {},
}))

vi.mock('@/services/e2eeIdentity', () => ({
  IDENTITY_LOADING: { state: 'loading', sendPair: null, decryptionKeys: [] },
  resolveIdentity: vi.fn(async () => ({
    state: identitaet.state,
    sendPair: identitaet.sendPair,
    decryptionKeys: identitaet.decryptionKeys,
  })),
  getRecipientPublicKey: vi.fn(async () => identitaet.empfaengerSchluessel),
  requireRecipientPublicKey: vi.fn(async (userId: number) => {
    if (!identitaet.empfaengerSchluessel) throw new MockRecipientKeyMissingError(userId)
    return identitaet.empfaengerSchluessel
  }),
  forgetRecipientPublicKey: vi.fn(),
  createIdentity: vi.fn(),
  unlockWithRecoveryKey: vi.fn(),
  rotateRecoveryKey: vi.fn(),
  clearIdentityMemory: vi.fn(),
  E2eeRecipientKeyMissingError: MockRecipientKeyMissingError,
  E2eeLockedError: class extends Error {},
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
    leereTestSpeicher()
    vi.clearAllMocks()
    setupUser()

    // Standardlage: Geraet entsperrt, Gegenseite hat einen Schluessel.
    identitaet.state = 'ready'
    identitaet.sendPair = { publicKeyJwk: '{"kty":"oct"}', privateKeyJwk: '{"kty":"oct"}' }
    identitaet.decryptionKeys = ['{"kty":"oct"}']
    identitaet.empfaengerSchluessel = 'mock-empfaenger-pub-key'

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


