import { describe, it, expect, vi, beforeEach } from 'vitest'
import { sendE2eeDeliveryReceipt } from './deliveryReceiptService'
import * as socialApi from '@/api/social'
import * as e2eeCrypto from '@/services/e2eeCrypto'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: vi.fn().mockResolvedValue({ id: 999, blind_mailbox_id: 'box-1' }),
  getE2eePublicKey: vi.fn().mockResolvedValue({ user_id: 2, username: 'bob', public_key: 'bob-pub-key' }),
}))

vi.mock('@/services/e2eeCrypto', () => ({
  getLocalKeyPair: vi.fn().mockResolvedValue({
    publicKeyJwk: '{"kty":"RSA","n":"test"}',
    privateKeyJwk: '{"kty":"RSA","d":"test"}',
  }),
  getOrGenerateLocalKeyPair: vi.fn().mockResolvedValue({
    publicKeyJwk: '{"kty":"RSA","n":"test"}',
    privateKeyJwk: '{"kty":"RSA","d":"test"}',
  }),
  encryptE2eeHybrid: vi.fn().mockResolvedValue('sv-e2ee-hybrid-v1:mock-hybrid-delivery'),
  encryptE2eeMessage: vi.fn().mockResolvedValue('sv-e2ee-v1:mock-symmetric-delivery'),
}))

describe('deliveryReceiptService', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessengerNotificationStore.setState({
      blockedUserIds: [],
    })
  })

  it('dispatches delivery_receipt envelope when receiving incoming message from another user', async () => {
    const success = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-abc',
      envelopeId: 42,
      senderUserId: 2,
      currentUserId: 1,
    })

    expect(success).toBe(true)
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({
        blind_mailbox_id: 'mailbox-abc',
        recipient_id: 2,
        is_control: true,
        control_type: 'delivery_receipt',
      })
    )
  })

  it('deduplicates delivery receipts for the same envelopeId', async () => {
    const first = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-abc',
      envelopeId: 101,
      senderUserId: 2,
      currentUserId: 1,
    })
    expect(first).toBe(true)
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledTimes(1)

    const second = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-abc',
      envelopeId: 101,
      senderUserId: 2,
      currentUserId: 1,
    })
    expect(second).toBe(false)
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledTimes(1)
  })

  it('does not send delivery receipt if sender is self', async () => {
    const success = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-abc',
      envelopeId: 102,
      senderUserId: 1,
      currentUserId: 1,
    })
    expect(success).toBe(false)
    expect(socialApi.relayE2eeEnvelope).not.toHaveBeenCalled()
  })

  it('does not send delivery receipt if sender is blocked (victim protection)', async () => {
    useMessengerNotificationStore.setState({
      blockedUserIds: [5],
    })

    const success = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-blocked',
      envelopeId: 103,
      senderUserId: 5,
      currentUserId: 1,
    })
    expect(success).toBe(false)
    expect(socialApi.relayE2eeEnvelope).not.toHaveBeenCalled()
  })

  it('falls back to symmetric encryptE2eeMessage when recipient has no public key', async () => {
    vi.mocked(socialApi.getE2eePublicKey).mockRejectedValueOnce(new Error('No key'))

    const success = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-sym',
      envelopeId: 104,
      senderUserId: 9,
      currentUserId: 1,
    })

    expect(success).toBe(true)
    expect(e2eeCrypto.encryptE2eeMessage).toHaveBeenCalledWith(
      expect.stringContaining('"type":"delivery_receipt"'),
      1,
      9
    )
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({
        blind_mailbox_id: 'mailbox-sym',
        recipient_id: 9,
        ciphertext_envelope: 'sv-e2ee-v1:mock-symmetric-delivery',
      })
    )
  })

  it('does not send delivery receipt for group mailboxes', async () => {
    useMessengerNotificationStore.setState({
      mailboxDirectory: {
        'group-box-1': {
          name: 'Dev Team',
          isGroup: true,
          groupId: 7,
        },
      },
    })

    const success = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'group-box-1',
      envelopeId: 200,
      senderUserId: 3,
      currentUserId: 1,
    })

    expect(success).toBe(false)
    expect(socialApi.relayE2eeEnvelope).not.toHaveBeenCalled()
  })
})
