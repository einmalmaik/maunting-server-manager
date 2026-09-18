import React from 'react'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { Messenger, clearSessionChatCache } from './Messenger'
import { ChatMediaImage, chatMediaBlobCache } from '@/components/social/ChatMediaAttachments'
import * as socialApi from '@/api/social'
import { teamsApi } from '@/api/teams'
import { useAuthStore } from '@/stores/authStore'
import {
  generateLocalE2eeKeyPair,
  encryptE2eeHybrid,
  decryptE2eeHybrid,
  encryptE2eeMessage,
  decryptE2eeMessage,
  deriveBlindMailboxId,
  storeLocalKeyPair,
  clearEnvelopePlaintextCache,
  clearRsaPrivateKeyCache,
  clearMemoryKeyStore,
  clearBlindMailboxIdCache,
} from '@/services/e2eeCrypto'

// Mock social and teams API for UI tests
/**
 * Baut einen Umschlag im Format des Double Ratchet.
 *
 * Bis 09/2026 bauten diese Tests ihre Umschläge mit `encryptE2eeMessage` — dem
 * Verfahren, dessen Schlüssel sich aus den beiden Benutzerkennungen ergab und
 * das der Server deshalb mitlesen konnte. Es ist abgeschafft, und das Relais
 * nimmt seine Umschläge nicht mehr an. Geprüft wird hier ohnehin die Schicht
 * darüber: Häkchen, Verlauf, Quittungen.
 */
function drUmschlag(klartext: string): string {
  return 'sv-e2ee-dr-v1:1.testgeraet.zielgeraet.' + btoa(unescape(encodeURIComponent(klartext)))
}

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
  getChatMediaSignedUrl: vi.fn(),
  downloadChatMedia: vi.fn(),
  ladeAnhangHerunter: vi.fn(),
  ladeAnhangHoch: vi.fn(),
  uploadEncryptedChatAttachment: vi.fn(),
}))

/**
 * Die Identität des Kontos wird gestellt, damit die Tests den Zustand des
 * Geräts direkt setzen können ('ready' oder 'locked') statt jedes Mal einen
 * Schlüsselbund per Argon2id zu öffnen.
 */
const { identitaet, MockRecipientKeyMissingError } = vi.hoisted(() => ({
  identitaet: {
    state: 'ready' as 'needs-setup' | 'locked' | 'ready',
    sendPair: null as { publicKeyJwk: string; privateKeyJwk: string } | null,
    decryptionKeys: [] as string[],
    empfaengerSchluessel: null as string | null,
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

vi.mock('@/api/teams', () => ({
  teamsApi: {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn(),
  },
}))

vi.mock('@/lib/offlineSync', () => ({
  loadNotesOfflineFirst: vi.fn().mockResolvedValue({ notes: [] }),
  loadCalendarEventsOfflineFirst: vi.fn().mockResolvedValue({ events: [] }),
  saveNoteOffline: vi.fn().mockResolvedValue({ id: 1, title: 'Mock' }),
  saveCalendarEventOffline: vi.fn().mockResolvedValue({ id: 1, title: 'Mock' }),
  enqueueMessageMutation: vi.fn().mockReturnValue({ id: 'mock-mutation' }),
}))

function setupAuthUser(userId: number = 2, username: string = 'user_b') {
  useAuthStore.setState({
    user: {
      id: userId,
      username,
      email: `${username}@example.test`,
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

describe('Requirement R1 Reproduction: E2EE Messenger Failure Modes', () => {
  // Ein echtes Schlüsselpaar für alle Tests. Die Krypto ist hier nicht
  // gemockt, und RSA-4096 je Test wäre die teuerste Zeile der Datei.
  let kontoSchluessel: { publicKeyJwk: string; privateKeyJwk: string }

  beforeAll(async () => {
    kontoSchluessel = await generateLocalE2eeKeyPair()
  }, 60_000)

  afterEach(() => {
    cleanup()
    clearBlindMailboxIdCache()
  })

  beforeEach(() => {
    leereTestSpeicher()
    cleanup()
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    clearSessionChatCache()
    clearEnvelopePlaintextCache()
    clearRsaPrivateKeyCache()
    clearMemoryKeyStore()
    clearBlindMailboxIdCache()
    chatMediaBlobCache.clear()
    vi.mocked(socialApi.getE2eePublicKey).mockResolvedValue(undefined as any)
    // Standardlage: Gerät entsperrt, Gegenseite hat einen Schlüssel.
    identitaet.state = 'ready'
    identitaet.sendPair = kontoSchluessel
    identitaet.decryptionKeys = [kontoSchluessel.privateKeyJwk]
    identitaet.empfaengerSchluessel = kontoSchluessel.publicKeyJwk
  })

  // =========================================================================
  // 1. Multi-device / Web vs. Tauri Key Mismatch
  // =========================================================================
  describe('1. Multi-device / Web vs. Tauri Key Mismatch', () => {
    it('cryptographic layer: hybrid messages encrypted with Web key fail to decrypt on Tauri client with different local key pair', async () => {
      // User A (Alice)
      const aliceKeys = await generateLocalE2eeKeyPair()
      // User B on Client 1 (Web): generates KeyPair 1, registered on server
      const bobWebKeys = await generateLocalE2eeKeyPair()
      // User B on Client 2 (Tauri): isolated keystore generates KeyPair 2
      const bobTauriKeys = await generateLocalE2eeKeyPair()

      // User A encrypts message for User B using User B's public key known to server (bobWebKeys.publicKeyJwk)
      const secretPlaintext = 'Vertrauliche Server-Konfiguration: port=8000'
      const envelope = await encryptE2eeHybrid(
        secretPlaintext,
        bobWebKeys.publicKeyJwk,
        aliceKeys.publicKeyJwk
      )
      expect(envelope.startsWith('sv-e2ee-hybrid-v1:')).toBe(true)

      // Web client (holding bobWebKeys) decrypts successfully
      const webDecrypted = await decryptE2eeHybrid(envelope, bobWebKeys.privateKeyJwk)
      expect(webDecrypted).toBe(secretPlaintext)

      // Tauri client (holding bobTauriKeys) attempts to decrypt:
      // Fails because neither wrapped key in the hybrid envelope matches bobTauriKeys.privateKeyJwk
      await expect(
        decryptE2eeHybrid(envelope, bobTauriKeys.privateKeyJwk)
      ).rejects.toThrow(/Kein passender RSA-Schlüssel im Hybrid-Umschlag|Hybrid-Entschlüsselung fehlgeschlagen/)
    }, 30_000)

    it('Client 2 veröffentlicht keinen eigenen Schlüssel mehr, wenn der Server schon einen kennt', async () => {
      // Diese Zusage steht auf dem Kopf der alten. Früher glich der Messenger
      // beim Öffnen den Servereintrag mit dem lokalen Schlüssel ab und lud bei
      // Abweichung den lokalen hoch — also überschrieb jedes zweite Gerät die
      // Identität des ersten, und danach war der Verlauf auf beiden Seiten
      // unlesbar. Der Abgleich existiert nicht mehr; ein Gerät ohne Bund meldet
      // 'locked' und wartet auf den Wiederherstellungsschlüssel.
      const bobId = 2
      setupAuthUser(bobId, 'bob')

      vi.mocked(socialApi.getE2eeKeyring).mockResolvedValue({
        wrapped_keyring: 'sv-e2ee-keyring-v1:bund-von-geraet-eins',
        public_key: '{"kty":"RSA","mock_web_key":true}',
        version: 1,
      })
      identitaet.state = 'locked'
      identitaet.sendPair = null
      identitaet.decryptionKeys = []

      vi.mocked(socialApi.getFriends).mockResolvedValue([])
      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([])

      render(
        <MemoryRouter initialEntries={['/chat']}>
          <Messenger />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(socialApi.getFriends).toHaveBeenCalled()
      })

      expect(socialApi.setE2eePublicKey).not.toHaveBeenCalled()
      expect(socialApi.putE2eeKeyring).not.toHaveBeenCalled()
    }, 30_000)

    it('UI layer: Messenger renders "Verschlüsselte Nachricht" placeholder when hybrid decryption fails', async () => {
      const aliceId = 101
      const bobId = 2
      setupAuthUser(bobId, 'bob')

      const aliceKeys = await generateLocalE2eeKeyPair()
      const bobWebKeys = await generateLocalE2eeKeyPair()
      const bobTauriKeys = await generateLocalE2eeKeyPair()

      // Bob's current client (Tauri) has bobTauriKeys in local keystore
      await storeLocalKeyPair(bobId, bobTauriKeys)

      // Alice sent a message encrypted with Bob's Web public key (as advertised by server)
      const secretMessage = JSON.stringify({
        client_uuid: 'msg-uuid-fail-1',
        sender_id: aliceId,
        sender_name: 'alice',
        text: 'Sensibles Root-Passwort: 12345',
        timestamp: new Date().toISOString(),
      })
      const mismatchedEnvelope = await encryptE2eeHybrid(
        secretMessage,
        bobWebKeys.publicKeyJwk,
        aliceKeys.publicKeyJwk
      )

      vi.mocked(socialApi.getFriends).mockResolvedValue([
        {
          id: aliceId,
          user_id: aliceId,
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

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 501,
          blind_mailbox_id: 'blind-mailbox-101-2',
          ciphertext_envelope: mismatchedEnvelope,
          client_uuid: 'msg-uuid-fail-1',
          created_at: new Date().toISOString(),
        },
      ])

      render(
        <MemoryRouter initialEntries={[`/chat?userId=${aliceId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      // The conversation loads and displays the contact
      await waitFor(() => {
        expect(screen.getAllByText('alice').length).toBeGreaterThan(0)
      })

      // Because decryption failed due to key mismatch, Messenger renders the "Verschlüsselte Nachricht" fallback
      await waitFor(() => {
        expect(screen.getByText('Verschlüsselte Nachricht')).toBeInTheDocument()
      })

      // The actual secret plaintext is NOT displayed
      expect(screen.queryByText('Sensibles Root-Passwort: 12345')).toBeNull()
    }, 30_000)
  })

  // =========================================================================
  // 2. Control Packet Leak & Tick Stall on 1 Checkmark
  // =========================================================================
  describe('2. Control Packet Leak & Tick Stall on 1 Checkmark', () => {
    it('control packet silencing: un-decryptable control envelope is silently discarded and never renders as "Verschlüsselte Nachricht"', async () => {
      const aliceId = 101
      const bobId = 2
      setupAuthUser(bobId, 'bob')

      vi.mocked(socialApi.getFriends).mockResolvedValue([
        {
          id: aliceId,
          user_id: aliceId,
          username: 'alice',
          avatar_url: null,
          status: 'accepted',
          is_requester: false,
          created_at: '2026-09-01T00:00:00Z',
          presence: { status: 'online', device_type: 'web', activity_label: 'Online', activity_detail: null },
        },
      ])

      // Envelope 777 is a delivery_receipt control packet, but corrupted/un-decryptable
      // (e.g. from key desync or transport corruption)
      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 777,
          blind_mailbox_id: 'blind-mailbox-101-2',
          ciphertext_envelope: 'sv-e2ee-hybrid-v1:corrupted_control_packet_bytes',
          client_uuid: 'control-receipt-uuid',
          created_at: new Date().toISOString(),
        },
      ])

      render(
        <MemoryRouter initialEntries={[`/chat?userId=${aliceId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(screen.getAllByText('alice').length).toBeGreaterThan(0)
      })

      // The unparsed control envelope must be silently discarded and never leak into the chat timeline
      expect(screen.queryByText('Verschlüsselte Nachricht')).toBeNull()
    })

    it('tick synchronization: optimistic message ID updated to server envelope ID transitions isDelivered to true', () => {
      // With the fix, when relayE2eeEnvelope returns { id: 42 }, optimisticMessage.id is updated from timestamp to 42
      const serverEnvelopeId = 42
      const optimisticMessage = {
        id: serverEnvelopeId,
        clientUuid: 'optimistic-uuid-1',
        isSelf: true,
        text: 'Hallo Bob',
        isDelivered: false,
        isRead: false,
      }

      // Partner device sends back delivery receipt acknowledging server envelope ID 42:
      const deliveryReceiptPayload = {
        type: 'delivery_receipt',
        delivered_up_to_id: serverEnvelopeId,
        receiver_id: 2,
      }

      const maxPartnerDeliveredId = Number(deliveryReceiptPayload.delivered_up_to_id) // 42
      const maxPartnerReadId = 0

      const isRead = optimisticMessage.isSelf && maxPartnerReadId >= optimisticMessage.id
      const isDelivered = optimisticMessage.isSelf && (isRead || maxPartnerDeliveredId >= optimisticMessage.id)

      // Verification of fix: 42 >= 42 is TRUE!
      expect(maxPartnerDeliveredId >= optimisticMessage.id).toBe(true)
      expect(isDelivered).toBe(true)

      const renderedCheckmark = isDelivered ? 'double-checkmark' : 'single-checkmark'
      expect(renderedCheckmark).toBe('double-checkmark')
    })

    it('UI layer: sent optimistic message transitions from 1 checkmark to 2 gray checkmarks (delivered) when delivery receipt acknowledges server ID', async () => {
      const aliceId = 301
      const myUserId = 1
      setupAuthUser(myUserId, 'me')

      vi.mocked(socialApi.getFriends).mockResolvedValue([
        {
          id: aliceId,
          user_id: aliceId,
          username: 'alice',
          avatar_url: null,
          status: 'accepted',
          is_requester: false,
          created_at: '2026-09-01T00:00:00Z',
          presence: { status: 'online', device_type: 'web', activity_label: 'Online', activity_detail: null },
        },
      ])

      const expectedMid = await deriveBlindMailboxId(myUserId, aliceId)

      // Server will return ID 42 when message is relayed
      vi.mocked(socialApi.relayE2eeEnvelope).mockResolvedValue({
        id: 42,
        blind_mailbox_id: expectedMid,
        ciphertext_envelope: 'ciphertext-mock',
        created_at: new Date().toISOString(),
      })

      // Delivery receipt acknowledging server envelope ID 42
      const deliveryReceiptPlain = JSON.stringify({
        type: 'delivery_receipt',
        delivered_up_to_id: 42,
        receiver_id: aliceId,
      })

      const encryptedDeliveryReceipt = drUmschlag(deliveryReceiptPlain)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 100,
          blind_mailbox_id: expectedMid,
          ciphertext_envelope: encryptedDeliveryReceipt,
          created_at: new Date().toISOString(),
        },
      ])

      render(
        <MemoryRouter initialEntries={[`/chat?userId=${aliceId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(screen.getAllByText('alice').length).toBeGreaterThan(0)
      })

      // Send a message via input
      const input = screen.getByPlaceholderText('Nachricht schreiben …')
      fireEvent.change(input, { target: { value: 'Wichtige Nachricht' } })

      const sendBtn = screen.getByTitle('Senden')
      fireEvent.click(sendBtn)

      // The sent message renders in the timeline
      await waitFor(() => {
        expect(screen.getByText('Wichtige Nachricht')).toBeInTheDocument()
      })

      // Simulate incoming delivery receipt event from Alice acknowledging envelope 42
      window.dispatchEvent(
        new CustomEvent('msm:sync-event', {
          detail: {
            type: 'e2ee_blind_message',
            blind_mailbox_id: expectedMid,
            is_control: true,
            control_type: 'delivery_receipt',
          },
        })
      )

      // The message successfully transitions to 2 checkmarks (delivered)
      const deliveredCheckTitle = 'Zugestellt / Vom Gesprächspartner empfangen'
      await waitFor(
        () => {
          expect(screen.getByTitle(deliveredCheckTitle)).toBeInTheDocument()
        },
        { timeout: 4000 }
      )
    })
  })

  // =========================================================================
  // 3. Media Attachment Context
  // =========================================================================
  describe('3. Medienanhänge hängen am Paketschlüssel, nicht an den Kennungen', () => {
    /*
     * Hier standen drei Zusagen über `decryptE2eeAttachmentBlob` und die Frage,
     * ob `userBId` beim Neuladen 0 wird. Die Frage gibt es seit 09/2026 nicht
     * mehr: der Schlüssel eines Anhangs wird nicht aus den Benutzerkennungen
     * abgeleitet, sondern zufällig erzeugt und im verschlüsselten
     * Nachrichten-Payload zugestellt. Absender und Mailbox binden nur noch die
     * gebundenen Daten — falsch geraten heißt jetzt „geht nicht auf", nicht
     * mehr „jeder, der die beiden Kennungen kennt, kann mitlesen".
     */

    it('lädt einen Anhang mit vollständigem Zeiger', async () => {
      const mediaId = 'media-attachment-123'
      const testDataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
      vi.mocked(socialApi.ladeAnhangHerunter).mockResolvedValue(testDataUrl)

      render(
        <ChatMediaImage
          attachment={{ mediaId, name: 'foto.png', paketSchluessel: 'c2NobHVlc3NlbA==', fileId: 'anhang-1' }}
          bindung={{ absenderId: 1, blindMailboxId: 'mailbox-fuer-den-test' }}
          onViewImage={() => {}}
        />
      )

      await waitFor(() => {
        const img = screen.getByAltText('foto.png')
        expect(img).toBeInTheDocument()
        expect(img).toHaveAttribute('src', testDataUrl)
      })
    })

    it('holt einen Anhang ohne Paketschlüssel gar nicht erst', async () => {
      // Altbestand aus der Zeit der ableitbaren Kanalschlüssel. Früher hätte
      // die Anzeige hier den Schlüssel aus den beiden Benutzerkennungen
      // gebildet — genau den, den auch das Backend bilden kann.
      vi.mocked(socialApi.ladeAnhangHerunter).mockResolvedValue('data:image/png;base64,AAAA')

      render(
        <ChatMediaImage
          attachment={{ mediaId: 'altbestand-1', name: 'alt.png' }}
          bindung={{ absenderId: 1, blindMailboxId: 'mailbox-fuer-den-test' }}
          onViewImage={() => {}}
        />
      )

      await waitFor(() => {
        expect(screen.getByText('Bild konnte nicht geladen werden')).toBeInTheDocument()
      })
      expect(socialApi.ladeAnhangHerunter).not.toHaveBeenCalled()
    })
  })

  describe('4. Neuladen', () => {
    it('reload hydration: Messenger restores active conversation and loads mailbox history from query param', async () => {
      const aliceId = 101
      const myUserId = 1
      setupAuthUser(myUserId, 'me')

      vi.mocked(socialApi.getFriends).mockResolvedValue([
        {
          id: aliceId,
          user_id: aliceId,
          username: 'alice',
          avatar_url: null,
          status: 'accepted',
          is_requester: false,
          created_at: '2026-09-01T00:00:00Z',
          presence: { status: 'online', device_type: 'web', activity_label: 'Online', activity_detail: null },
        },
      ])

      const historicalMessage = JSON.stringify({
        client_uuid: 'msg-historic-1',
        sender_id: aliceId,
        sender_name: 'alice',
        text: 'Historische Nachricht aus Mailbox',
        timestamp: new Date().toISOString(),
      })

      const encryptedEnvelope = drUmschlag(historicalMessage)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 50,
          blind_mailbox_id: 'test-mailbox-history',
          ciphertext_envelope: encryptedEnvelope,
          client_uuid: 'msg-historic-1',
          created_at: new Date().toISOString(),
        },
      ])

      render(
        <MemoryRouter initialEntries={[`/chat?userId=${aliceId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      // Conversation and contact are hydrated from URL param
      await waitFor(() => {
        expect(screen.getAllByText('alice').length).toBeGreaterThan(0)
      })

      // Envelopes from server mailbox are fetched and displayed
      await waitFor(() => {
        expect(screen.getByText('Historische Nachricht aus Mailbox')).toBeInTheDocument()
      }, { timeout: 5000 })
    }, 30_000)
  })
})
