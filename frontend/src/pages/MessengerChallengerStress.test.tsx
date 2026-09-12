import React from 'react'
import { render, screen, waitFor, fireEvent, cleanup, act } from '@testing-library/react'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { Messenger, clearSessionChatCache } from './Messenger'
import * as socialApi from '@/api/social'
import { teamsApi } from '@/api/teams'
import { useAuthStore } from '@/stores/authStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import {
  deriveBlindMailboxId,
  encryptE2eeMessage,
  decryptE2eeMessage,
  encryptE2eeHybrid,
  decryptE2eeHybrid,
  clearEnvelopePlaintextCache,
  clearBlindMailboxIdCache,
  generateLocalE2eeKeyPair,
  storeLocalKeyPair,
} from '@/services/e2eeCrypto'

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
  getChatMediaSignedUrl: vi.fn(),
  downloadChatMedia: vi.fn(),
  downloadAndDecryptChatAttachment: vi.fn(),
  uploadEncryptedChatAttachment: vi.fn(),
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

function setupAuthUser(userId: number = 1, username: string = 'me') {
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
      device_notifications: true,
      role_id: null,
      created_at: '2026-08-28T00:00:00Z',
    },
    isAuthenticated: true,
  })
}

describe('Empirical Challenger: Delivery Receipt Synchronization & Reload Hydration Stress Tests', () => {
  const myUserId = 1
  const aliceId = 101
  const bobId = 102
  const charlieId = 103

  const singleTickTitle = 'Nicht zugestellt (noch nicht beim Empfänger angekommen)'
  const doubleGrayTickTitle = 'Zugestellt / Vom Gesprächspartner empfangen'
  const doubleBlueTickTitle = 'Gelesen vom Gesprächspartner'

  afterEach(() => {
    cleanup()
    clearBlindMailboxIdCache()
  })

  beforeEach(() => {
    cleanup()
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    clearSessionChatCache()
    clearEnvelopePlaintextCache()
    setupAuthUser(myUserId, 'me')

    useMessengerNotificationStore.setState({
      blockedUserIds: [],
      mutedMailboxIds: [],
      unreadCountByMailbox: {},
      activeMailboxId: null,
    })

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
      {
        id: bobId,
        user_id: bobId,
        username: 'bob',
        avatar_url: null,
        status: 'accepted',
        is_requester: false,
        created_at: '2026-09-01T00:00:00Z',
        presence: { status: 'online', device_type: 'web', activity_label: 'Online', activity_detail: null },
      },
      {
        id: charlieId,
        user_id: charlieId,
        username: 'charlie',
        avatar_url: null,
        status: 'accepted',
        is_requester: false,
        created_at: '2026-09-01T00:00:00Z',
        presence: { status: 'online', device_type: 'web', activity_label: 'Online', activity_detail: null },
      },
    ])

    vi.mocked(socialApi.getGroups).mockResolvedValue([])
    vi.mocked(socialApi.getDirectChats).mockResolvedValue([])
    vi.mocked(socialApi.getStories).mockResolvedValue([])
    vi.mocked(socialApi.getPublicProfiles).mockResolvedValue([])
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([])
  })

  // =========================================================================
  // CHALLENGE 1: Race conditions & Interleaved Out-of-Order Delivery Receipts
  // =========================================================================
  describe('Challenge 1: Rapid outgoing messages and out-of-order delivery receipts', () => {
    it('handles out-of-order delivery receipts (ID 5 then ID 2 then ID 10) without tick regression', async () => {
      const mailboxId = await deriveBlindMailboxId(myUserId, aliceId)

      let serverIdCounter = 0
      const assignedIds: number[] = [2, 5, 10]
      vi.mocked(socialApi.relayE2eeEnvelope).mockImplementation(async (payload) => {
        const id = assignedIds[serverIdCounter++] || 99
        return {
          id,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: payload.ciphertext_envelope,
          client_uuid: payload.client_uuid,
          created_at: new Date().toISOString(),
        }
      })

      render(
        <MemoryRouter initialEntries={[`/chat?userId=${aliceId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(screen.getAllByText('alice').length).toBeGreaterThan(0)
      })

      const input = screen.getByPlaceholderText('Nachricht schreiben …')
      const sendBtn = screen.getByTitle('Senden')

      // Rapidly send Message 1 (will get ID 2)
      fireEvent.change(input, { target: { value: 'Rapid Message 1' } })
      fireEvent.click(sendBtn)
      await waitFor(() => expect(screen.getByText('Rapid Message 1')).toBeInTheDocument())

      // Rapidly send Message 2 (will get ID 5)
      fireEvent.change(input, { target: { value: 'Rapid Message 2' } })
      fireEvent.click(sendBtn)
      await waitFor(() => expect(screen.getByText('Rapid Message 2')).toBeInTheDocument())

      // Rapidly send Message 3 (will get ID 10)
      fireEvent.change(input, { target: { value: 'Rapid Message 3' } })
      fireEvent.click(sendBtn)
      await waitFor(() => expect(screen.getByText('Rapid Message 3')).toBeInTheDocument())

      // All 3 messages initially display 1 single checkmark (sent / not yet delivered)
      await waitFor(() => {
        const singleTicks = screen.getAllByTitle(singleTickTitle)
        expect(singleTicks.length).toBe(3)
      })

      // Interleaved receipt 1: Delivery receipt acknowledging ID 5 arrives
      const receipt5Plain = JSON.stringify({
        type: 'delivery_receipt',
        delivered_up_to_id: 5,
        receiver_id: aliceId,
      })
      const encryptedReceipt5 = await encryptE2eeMessage(receipt5Plain, myUserId, aliceId)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 50,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: encryptedReceipt5,
          client_uuid: 'receipt-5',
          created_at: new Date().toISOString(),
        },
      ])

      act(() => {
        window.dispatchEvent(
          new CustomEvent('msm:sync-event', {
            detail: {
              type: 'e2ee_blind_message',
              blind_mailbox_id: mailboxId,
              is_control: true,
              control_type: 'delivery_receipt',
            },
          })
        )
      })

      // Message 1 (ID 2 <= 5) and Message 2 (ID 5 <= 5) transition to double checkmark
      // Message 3 (ID 10 > 5) remains single checkmark
      await waitFor(() => {
        const doubleTicks = screen.getAllByTitle(doubleGrayTickTitle)
        expect(doubleTicks.length).toBe(2)
        const singleTicks = screen.getAllByTitle(singleTickTitle)
        expect(singleTicks.length).toBe(1)
      })

      // Interleaved receipt 2: Out-of-order receipt acknowledging ID 2 arrives (ID 2 < ID 5)
      const receipt2Plain = JSON.stringify({
        type: 'delivery_receipt',
        delivered_up_to_id: 2,
        receiver_id: aliceId,
      })
      const encryptedReceipt2 = await encryptE2eeMessage(receipt2Plain, myUserId, aliceId)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 50,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: encryptedReceipt5,
          client_uuid: 'receipt-5',
          created_at: new Date().toISOString(),
        },
        {
          id: 51,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: encryptedReceipt2,
          client_uuid: 'receipt-2',
          created_at: new Date().toISOString(),
        },
      ])

      act(() => {
        window.dispatchEvent(
          new CustomEvent('msm:sync-event', {
            detail: {
              type: 'e2ee_blind_message',
              blind_mailbox_id: mailboxId,
              is_control: true,
              control_type: 'delivery_receipt',
            },
          })
        )
      })

      // CRITICAL ASSERTION: Out-of-order receipt for ID 2 MUST NOT cause Message 2 (ID 5) to regress!
      // Double ticks must remain at 2, single tick at 1.
      await waitFor(() => {
        const doubleTicks = screen.getAllByTitle(doubleGrayTickTitle)
        expect(doubleTicks.length).toBe(2)
        const singleTicks = screen.getAllByTitle(singleTickTitle)
        expect(singleTicks.length).toBe(1)
      })

      // Interleaved receipt 3: Receipt acknowledging ID 10 arrives
      const receipt10Plain = JSON.stringify({
        type: 'delivery_receipt',
        delivered_up_to_id: 10,
        receiver_id: aliceId,
      })
      const encryptedReceipt10 = await encryptE2eeMessage(receipt10Plain, myUserId, aliceId)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 50,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: encryptedReceipt5,
          client_uuid: 'receipt-5',
          created_at: new Date().toISOString(),
        },
        {
          id: 51,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: encryptedReceipt2,
          client_uuid: 'receipt-2',
          created_at: new Date().toISOString(),
        },
        {
          id: 52,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: encryptedReceipt10,
          client_uuid: 'receipt-10',
          created_at: new Date().toISOString(),
        },
      ])

      act(() => {
        window.dispatchEvent(
          new CustomEvent('msm:sync-event', {
            detail: {
              type: 'e2ee_blind_message',
              blind_mailbox_id: mailboxId,
              is_control: true,
              control_type: 'delivery_receipt',
            },
          })
        )
      })

      // All 3 messages are now acknowledged and show 2 checkmarks
      await waitFor(() => {
        const doubleTicks = screen.getAllByTitle(doubleGrayTickTitle)
        expect(doubleTicks.length).toBe(3)
        expect(screen.queryByTitle(singleTickTitle)).not.toBeInTheDocument()
      })
    })

    it('immediately resolves optimistic message to 2 checkmarks if delivery receipt arrived before relay resolution', async () => {
      const mailboxId = await deriveBlindMailboxId(myUserId, aliceId)

      let resolveRelay!: (val: any) => void
      const relayPromise = new Promise((resolve) => {
        resolveRelay = resolve
      })
      vi.mocked(socialApi.relayE2eeEnvelope).mockReturnValue(relayPromise as any)

      render(
        <MemoryRouter initialEntries={[`/chat?userId=${aliceId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(screen.getAllByText('alice').length).toBeGreaterThan(0)
      })

      const input = screen.getByPlaceholderText('Nachricht schreiben …')
      const sendBtn = screen.getByTitle('Senden')

      // Send message - relay remains pending
      fireEvent.change(input, { target: { value: 'In-Flight Message' } })
      fireEvent.click(sendBtn)
      await waitFor(() => expect(screen.getByText('In-Flight Message')).toBeInTheDocument())
      expect(screen.getByTitle(singleTickTitle)).toBeInTheDocument()

      // Delivery receipt acknowledging up to ID 25 arrives while relay is still in flight!
      const receiptPlain = JSON.stringify({
        type: 'delivery_receipt',
        delivered_up_to_id: 25,
        receiver_id: aliceId,
      })
      const encryptedReceipt = await encryptE2eeMessage(receiptPlain, myUserId, aliceId)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 99,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: encryptedReceipt,
          client_uuid: 'receipt-25',
          created_at: new Date().toISOString(),
        },
      ])

      act(() => {
        window.dispatchEvent(
          new CustomEvent('msm:sync-event', {
            detail: {
              type: 'e2ee_blind_message',
              blind_mailbox_id: mailboxId,
              is_control: true,
              control_type: 'delivery_receipt',
            },
          })
        )
      })

      // Now relay resolves with ID 20 (which is <= 25)
      act(() => {
        resolveRelay({
          id: 20,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: 'cipher',
          client_uuid: 'mock-uuid',
          created_at: new Date().toISOString(),
        })
      })

      // Message immediately renders double checkmark upon relay resolution!
      await waitFor(() => {
        expect(screen.getByTitle(doubleGrayTickTitle)).toBeInTheDocument()
        expect(screen.queryByTitle(singleTickTitle)).not.toBeInTheDocument()
      })
    })
  })

  // =========================================================================
  // CHALLENGE 2: Conversation Switching & Asymmetric Envelope ID Isolation
  // =========================================================================
  describe('Challenge 2: Conversation switching with asymmetric envelope IDs', () => {
    it('switching from Contact A (high IDs: 500) to Contact B (low IDs: 2) does NOT cause false double-ticks or suppress delivery receipts', async () => {
      const aliceMid = await deriveBlindMailboxId(myUserId, aliceId)
      const bobMid = await deriveBlindMailboxId(myUserId, bobId)

      // Alice's chat has high envelope IDs (e.g. 500)
      const aliceMsgPlain = JSON.stringify({
        client_uuid: 'alice-msg-500',
        sender_id: myUserId,
        text: 'Message to Alice with high ID',
        timestamp: new Date().toISOString(),
      })
      const aliceCipher = await encryptE2eeMessage(aliceMsgPlain, myUserId, aliceId)

      const aliceReceiptPlain = JSON.stringify({
        type: 'delivery_receipt',
        delivered_up_to_id: 500,
        receiver_id: aliceId,
      })
      const aliceReceiptCipher = await encryptE2eeMessage(aliceReceiptPlain, myUserId, aliceId)

      // Configure fetch envelopes based on active mailbox
      vi.mocked(socialApi.fetchE2eeEnvelopes).mockImplementation(async (mid) => {
        if (mid === aliceMid) {
          return [
            {
              id: 500,
              blind_mailbox_id: aliceMid,
              ciphertext_envelope: aliceCipher,
              client_uuid: 'alice-msg-500',
              created_at: new Date().toISOString(),
            },
            {
              id: 501,
              blind_mailbox_id: aliceMid,
              ciphertext_envelope: aliceReceiptCipher,
              client_uuid: 'receipt-500',
              created_at: new Date().toISOString(),
            },
          ]
        }
        if (mid === bobMid) {
          return []
        }
        return []
      })

      render(
        <MemoryRouter initialEntries={[`/chat?userId=${aliceId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      // Step 1: Open Alice and verify Alice has double checkmarks for envelope 500
      await waitFor(() => {
        expect(screen.getByText('Message to Alice with high ID')).toBeInTheDocument()
        expect(screen.getByTitle(doubleGrayTickTitle)).toBeInTheDocument()
      })

      // Step 2: Switch to Bob
      const bobButton = screen.getByRole('button', { name: /bob/i })
      fireEvent.click(bobButton)

      // Bob's chat loads
      await waitFor(() => {
        expect(screen.queryByText('Message to Alice with high ID')).not.toBeInTheDocument()
      })

      // Relay will return a LOW envelope ID (ID 2) for Bob
      vi.mocked(socialApi.relayE2eeEnvelope).mockResolvedValueOnce({
        id: 2,
        blind_mailbox_id: bobMid,
        ciphertext_envelope: 'bob-cipher',
        client_uuid: 'bob-client-1',
        created_at: new Date().toISOString(),
      })

      const input = screen.getByPlaceholderText('Nachricht schreiben …')
      const sendBtn = screen.getByTitle('Senden')

      fireEvent.change(input, { target: { value: 'Hello Bob low ID' } })
      fireEvent.click(sendBtn)

      await waitFor(() => {
        expect(screen.getByText('Hello Bob low ID')).toBeInTheDocument()
      })

      // CRITICAL ASSERTION: Message to Bob (ID 2) MUST NOT show false double checkmarks!
      // If maxPartnerDeliveredIdRef was not reset from Alice's 500, 500 >= 2 would falsely mark it delivered.
      await waitFor(() => {
        expect(screen.getByTitle(singleTickTitle)).toBeInTheDocument()
        expect(screen.queryByTitle(doubleGrayTickTitle)).not.toBeInTheDocument()
      })

      // Step 3: Test that incoming message from Bob with low ID (ID 3) triggers a delivery receipt
      // and is NOT suppressed! (If highestIncomingIdDeliveredRef was stuck at 500, 3 > 500 would be false and suppress it)
      const incomingBobPlain = JSON.stringify({
        client_uuid: 'bob-incoming-3',
        sender_id: bobId,
        text: 'Bob answers with ID 3',
        timestamp: new Date().toISOString(),
      })
      const incomingBobCipher = await encryptE2eeMessage(incomingBobPlain, myUserId, bobId)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockImplementation(async (mid) => {
        if (mid === bobMid) {
          return [
            {
              id: 3,
              blind_mailbox_id: bobMid,
              ciphertext_envelope: incomingBobCipher,
              client_uuid: 'bob-incoming-3',
              created_at: new Date().toISOString(),
            },
          ]
        }
        return []
      })

      // Clear relay mock calls to isolate delivery receipt dispatch
      vi.mocked(socialApi.relayE2eeEnvelope).mockClear()
      vi.mocked(socialApi.relayE2eeEnvelope).mockResolvedValue({
        id: 999,
        blind_mailbox_id: bobMid,
        ciphertext_envelope: 'receipt-ack',
        created_at: new Date().toISOString(),
      })

      act(() => {
        window.dispatchEvent(
          new CustomEvent('msm:sync-event', {
            detail: {
              type: 'e2ee_blind_message',
              blind_mailbox_id: bobMid,
              sender_user_id: bobId,
              recipient_id: myUserId,
            },
          })
        )
      })

      await waitFor(() => {
        expect(screen.getByText('Bob answers with ID 3')).toBeInTheDocument()
      })

      // Verify that delivery receipt was dispatched acknowledging Bob's low envelope ID 3!
      await waitFor(() => {
        expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
          expect.objectContaining({
            blind_mailbox_id: bobMid,
            recipient_id: bobId,
            is_control: true,
            control_type: 'delivery_receipt',
          })
        )
      })

      // Step 4: Bob sends delivery receipt acknowledging ID 2
      const bobReceiptPlain = JSON.stringify({
        type: 'delivery_receipt',
        delivered_up_to_id: 2,
        receiver_id: bobId,
      })
      const bobReceiptCipher = await encryptE2eeMessage(bobReceiptPlain, myUserId, bobId)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockImplementation(async (mid) => {
        if (mid === bobMid) {
          return [
            {
              id: 3,
              blind_mailbox_id: bobMid,
              ciphertext_envelope: incomingBobCipher,
              client_uuid: 'bob-incoming-3',
              created_at: new Date().toISOString(),
            },
            {
              id: 4,
              blind_mailbox_id: bobMid,
              ciphertext_envelope: bobReceiptCipher,
              client_uuid: 'bob-receipt-2',
              created_at: new Date().toISOString(),
            },
          ]
        }
        return []
      })

      act(() => {
        window.dispatchEvent(
          new CustomEvent('msm:sync-event', {
            detail: {
              type: 'e2ee_blind_message',
              blind_mailbox_id: bobMid,
              is_control: true,
              control_type: 'delivery_receipt',
            },
          })
        )
      })

      // Outgoing message to Bob now properly transitions to double checkmark
      await waitFor(() => {
        expect(screen.getByTitle(doubleGrayTickTitle)).toBeInTheDocument()
      })

      // Step 5: Switch back to Alice and verify Alice's messages are intact with double checkmarks
      const aliceButton = screen.getByRole('button', { name: /alice/i })
      fireEvent.click(aliceButton)

      await waitFor(() => {
        expect(screen.getByText('Message to Alice with high ID')).toBeInTheDocument()
        expect(screen.getByTitle(doubleGrayTickTitle)).toBeInTheDocument()
      })
    })
  })

  // =========================================================================
  // CHALLENGE 3: Reload Hydration with URL Query Parameters & Clean Decryption
  // =========================================================================
  describe('Challenge 3: Reload hydration with URL query parameters and clean decryption', () => {
    it('restores conversation immediately from URL query param and cleanly decrypts hybrid and legacy envelopes without placeholder spam', async () => {
      const charlieMid = await deriveBlindMailboxId(myUserId, charlieId)

      // Keypair for Charlie and MyUser
      const myKeys = await generateLocalE2eeKeyPair()
      const charlieKeys = await generateLocalE2eeKeyPair()
      await storeLocalKeyPair(myUserId, myKeys)

      // Envelope 1: Plain legacy/direct message from Charlie
      const msg1Plain = JSON.stringify({
        client_uuid: 'charlie-msg-1',
        sender_id: charlieId,
        sender_name: 'charlie',
        text: 'Willkommen im Chat Charlie!',
        timestamp: new Date().toISOString(),
      })
      const msg1Cipher = await encryptE2eeMessage(msg1Plain, myUserId, charlieId)

      // Envelope 2: Outgoing message from Me to Charlie
      const msg2Plain = JSON.stringify({
        client_uuid: 'my-msg-2',
        sender_id: myUserId,
        sender_name: 'me',
        text: 'Danke Charlie, alles klar!',
        timestamp: new Date().toISOString(),
      })
      const msg2Cipher = await encryptE2eeMessage(msg2Plain, myUserId, charlieId)

      // Envelope 3: Delivery receipt acknowledging Envelope 2
      const receiptPlain = JSON.stringify({
        type: 'delivery_receipt',
        delivered_up_to_id: 20,
        receiver_id: charlieId,
      })
      const receiptCipher = await encryptE2eeMessage(receiptPlain, myUserId, charlieId)

      // Envelope 4: Hybrid E2EE encrypted message from Charlie to Me
      const hybridPayload = JSON.stringify({
        client_uuid: 'charlie-hybrid-4',
        sender_id: charlieId,
        sender_name: 'charlie',
        text: 'Dies ist eine sichere Hybrid-Nachricht',
        timestamp: new Date().toISOString(),
      })
      const hybridCipher = await encryptE2eeHybrid(
        hybridPayload,
        myKeys.publicKeyJwk,
        charlieKeys.publicKeyJwk
      )

      // Envelope 5: Control envelope with is_control: true (must be silenced, never render placeholder)
      const controlPlain = JSON.stringify({
        type: 'heartbeat_signal',
        timestamp: new Date().toISOString(),
      })
      const controlCipher = await encryptE2eeMessage(controlPlain, myUserId, charlieId)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockImplementation(async (mid) => {
        if (mid === charlieMid) {
          return [
            {
              id: 10,
              blind_mailbox_id: charlieMid,
              ciphertext_envelope: msg1Cipher,
              client_uuid: 'charlie-msg-1',
              created_at: '2026-09-12T01:00:00Z',
            },
            {
              id: 20,
              blind_mailbox_id: charlieMid,
              ciphertext_envelope: msg2Cipher,
              client_uuid: 'my-msg-2',
              created_at: '2026-09-12T01:01:00Z',
            },
            {
              id: 25,
              blind_mailbox_id: charlieMid,
              ciphertext_envelope: receiptCipher,
              client_uuid: 'charlie-deliv-receipt',
              created_at: '2026-09-12T01:02:00Z',
            },
            {
              id: 30,
              blind_mailbox_id: charlieMid,
              ciphertext_envelope: hybridCipher,
              client_uuid: 'charlie-hybrid-4',
              created_at: '2026-09-12T01:03:00Z',
            },
            {
              id: 35,
              blind_mailbox_id: charlieMid,
              ciphertext_envelope: controlCipher,
              client_uuid: 'ctrl-heartbeat',
              is_control: true,
              created_at: '2026-09-12T01:04:00Z',
            },
          ]
        }
        return []
      })

      // Simulate cold page load directly on /chat?userId=103
      render(
        <MemoryRouter initialEntries={[`/chat?userId=${charlieId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      // 1. Charlie is hydrated automatically as active contact
      await waitFor(() => {
        expect(screen.getAllByText('charlie').length).toBeGreaterThan(0)
      })

      // 2. Both direct message and hybrid message decrypt and display cleanly
      await waitFor(() => {
        expect(screen.getByText('Willkommen im Chat Charlie!')).toBeInTheDocument()
        expect(screen.getByText('Danke Charlie, alles klar!')).toBeInTheDocument()
        expect(screen.getByText('Dies ist eine sichere Hybrid-Nachricht')).toBeInTheDocument()
      }, { timeout: 10000 })

      // 3. Outgoing message (ID 20) is hydrated with double ticks because Envelope 25 was in mailbox history
      await waitFor(() => {
        expect(screen.getByTitle(doubleGrayTickTitle)).toBeInTheDocument()
      })

      // 4. Zero instances of "Verschlüsselte Nachricht" placeholder spam
      expect(screen.queryByText(/Verschlüsselte Nachricht/i)).not.toBeInTheDocument()
    })
  })

  // =========================================================================
  // CHALLENGE 4: Group & Blocked Contact Invariants
  // =========================================================================
  describe('Challenge 4: Invariants for group conversations and blocked contacts', () => {
    it('does not send delivery receipt when sender is blocked', async () => {
      const mailboxId = await deriveBlindMailboxId(myUserId, bobId)
      useMessengerNotificationStore.setState({
        blockedUserIds: [bobId],
      })

      const bobMsgPlain = JSON.stringify({
        client_uuid: 'bob-blocked-1',
        sender_id: bobId,
        text: 'Spam message from blocked Bob',
        timestamp: new Date().toISOString(),
      })
      const bobCipher = await encryptE2eeMessage(bobMsgPlain, myUserId, bobId)

      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
        {
          id: 77,
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: bobCipher,
          client_uuid: 'bob-blocked-1',
          created_at: new Date().toISOString(),
        },
      ])

      render(
        <MemoryRouter initialEntries={[`/chat?userId=${bobId}`]}>
          <Messenger />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(screen.getByText('Spam message from blocked Bob')).toBeInTheDocument()
      })

      // Delivery receipt must NEVER be sent to blocked contact
      expect(socialApi.relayE2eeEnvelope).not.toHaveBeenCalledWith(
        expect.objectContaining({
          control_type: 'delivery_receipt',
        })
      )
    })
  })
})
