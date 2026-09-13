import { describe, it, expect, vi, beforeEach } from 'vitest'
import { sendE2eeDeliveryReceipt, deliveredEnvelopeIds } from './deliveryReceiptService'
import * as socialApi from '@/api/social'
import * as e2eeIdentity from '@/services/e2eeIdentity'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: vi.fn().mockResolvedValue({ id: 999, blind_mailbox_id: 'box-1' }),
  fetchE2eeEnvelopes: vi.fn().mockResolvedValue([]),
}))

vi.mock('@/services/e2eeCrypto', () => ({
  encryptE2eeHybrid: vi.fn().mockResolvedValue('sv-e2ee-hybrid-v1:mock-hybrid-delivery'),
}))

vi.mock('@/services/e2eeIdentity', () => ({
  resolveIdentity: vi.fn().mockResolvedValue({
    state: 'ready',
    sendPair: {
      publicKeyJwk: '{"kty":"RSA","n":"test"}',
      privateKeyJwk: '{"kty":"RSA","d":"test"}',
    },
    decryptionKeys: ['{"kty":"RSA","d":"test"}'],
  }),
  getRecipientPublicKey: vi.fn().mockResolvedValue('bob-pub-key'),
}))

describe('deliveryReceiptService', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    deliveredEnvelopeIds.clear()
    useMessengerNotificationStore.setState({
      blockedUserIds: [],
      mailboxDirectory: {},
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

  it('sendet keine Quittung, wenn der Empfänger keinen Schlüssel hinterlegt hat', async () => {
    // Früher fiel der Dienst hier auf `encryptE2eeMessage` zurück. Dessen
    // Schlüssel ergibt sich allein aus den beiden Benutzerkennungen, die das
    // Backend beim Relais ohnehin kennt — die Quittung wäre für den Server
    // lesbar gewesen. Ein fehlendes graues Häkchen ist der bessere Preis.
    vi.mocked(e2eeIdentity.getRecipientPublicKey).mockResolvedValueOnce(null)

    const success = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-sym',
      envelopeId: 104,
      senderUserId: 9,
      currentUserId: 1,
    })

    expect(success).toBe(false)
    expect(socialApi.relayE2eeEnvelope).not.toHaveBeenCalled()
    // Nicht als zugestellt vermerken, damit ein späterer Versuch nachziehen kann.
    expect(deliveredEnvelopeIds.has(104)).toBe(false)
  })

  it('sendet keine Quittung, solange der Schlüsselbund auf diesem Gerät gesperrt ist', async () => {
    vi.mocked(e2eeIdentity.resolveIdentity).mockResolvedValueOnce({
      state: 'locked',
      sendPair: null,
      decryptionKeys: [],
    })

    const success = await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-locked',
      envelopeId: 105,
      senderUserId: 9,
      currentUserId: 1,
    })

    expect(success).toBe(false)
    expect(socialApi.relayE2eeEnvelope).not.toHaveBeenCalled()
    expect(deliveredEnvelopeIds.has(105)).toBe(false)
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
