import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  sendE2eeDeliveryReceipt,
  checkAndDispatchPendingDeliveryReceipts,
  deliveredEnvelopeIds,
  QUITTUNG_PRAEFIX,
} from './deliveryReceiptService'
import * as socialApi from '@/api/social'
import * as e2eeGeraet from '@/services/e2eeGeraet'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: vi.fn().mockResolvedValue({ id: 999, blind_mailbox_id: 'box-1' }),
  fetchE2eeEnvelopes: vi.fn().mockResolvedValue([]),
  syncE2eeMailboxes: vi.fn().mockResolvedValue({ mailboxes: [] }),
}))

vi.mock('@/services/e2eeCrypto', () => ({
  encryptE2eeHybrid: vi.fn().mockResolvedValue('sv-e2ee-hybrid-v1:mock-hybrid-delivery'),
}))

vi.mock('@/services/e2eeGeraet', () => ({
  eigenesGeraet: vi.fn().mockResolvedValue({
    kennung: 'a1b2c3d4e5f60718',
    paar: {
      publicKeyJwk: '{"kty":"RSA","n":"test"}',
      privateKeyJwk: '{"kty":"RSA","d":"test"}',
    },
  }),
  geraeteVon: vi.fn().mockResolvedValue([
    { device_id: 'bob-geraet-0001', public_key: 'bob-pub-key', label: '' },
  ]),
}))

describe('deliveryReceiptService', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    deliveredEnvelopeIds.clear()
    // Der Quittungsstand überlebt absichtlich das Neuladen. Zwischen zwei
    // Prüfungen darf er das nicht, sonst hängt das Ergebnis an der
    // Reihenfolge der Kennungen.
    localStorage.clear()
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

  it('kennzeichnet die Quittung, damit sie sich selbst wiedererkennt', async () => {
    await sendE2eeDeliveryReceipt({
      blindMailboxId: 'mailbox-abc',
      envelopeId: 42,
      senderUserId: 2,
      currentUserId: 1,
    })

    const auftrag = vi.mocked(socialApi.relayE2eeEnvelope).mock.calls[0][0] as { client_uuid: string }
    expect(auftrag.client_uuid.startsWith(QUITTUNG_PRAEFIX)).toBe(true)
    expect(auftrag.client_uuid.length).toBeLessThanOrEqual(64)
  })

  it('quittiert keine Quittungen', async () => {
    // Eine Quittung ist selbst ein Umschlag in derselben Mailbox. Ohne diese
    // Grenze quittierte der Nachzügler-Abgleich sie erneut, und die Mailbox
    // wuchs bei jedem Ladevorgang um ihren eigenen Bestand — am laufenden
    // System hundert Umschläge je Neuladen, ohne dass jemand schrieb.
    useMessengerNotificationStore.setState({
      blockedUserIds: [],
      mailboxDirectory: { 'mailbox-abc': { isGroup: false, userId: 2 } as never },
    })
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      { id: 1, client_uuid: `${QUITTUNG_PRAEFIX}aaa`, ciphertext_envelope: 'x', created_at: '' },
      { id: 2, client_uuid: 'echte-nachricht', ciphertext_envelope: 'y', created_at: '' },
    ] as never)

    await checkAndDispatchPendingDeliveryReceipts(1)

    const quittierte = vi
      .mocked(socialApi.relayE2eeEnvelope)
      .mock.calls.map((c) => (c[0] as { client_uuid: string }).client_uuid)
    expect(quittierte).toHaveLength(1)
    expect(deliveredEnvelopeIds.has(2)).toBe(true)
    expect(deliveredEnvelopeIds.has(1)).toBe(false)
  })

  it('schickt eine Quittung je Mailbox, nicht eine je Umschlag', async () => {
    // Gemessen am laufenden System: 2221 von 3444 Umschlägen waren
    // Quittungen. Die Schleife hier quittierte jeden Umschlag einzeln, und
    // das Fenster fasst hundert — verdrängt wurde, was sich nicht
    // nachbestellen lässt.
    useMessengerNotificationStore.setState({
      blockedUserIds: [],
      mailboxDirectory: { 'mailbox-abc': { isGroup: false, userId: 2 } as never },
    })
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue(
      Array.from({ length: 100 }, (_, i) => ({
        id: i + 1,
        client_uuid: `nachricht-${i}`,
        ciphertext_envelope: 'x',
        created_at: '',
      })) as never,
    )

    await checkAndDispatchPendingDeliveryReceipts(1)

    expect(vi.mocked(socialApi.relayE2eeEnvelope)).toHaveBeenCalledTimes(1)
  })

  it('quittiert nach einem Neuladen nicht noch einmal', async () => {
    // `deliveredEnvelopeIds` liegt im `sessionStorage` und ist nach einem
    // Neuladen leer. Genau das ließ die Mailbox bei jedem Ladevorgang um
    // ihren eigenen Bestand wachsen; der Merker in `quittungsstand` hält
    // dagegen.
    useMessengerNotificationStore.setState({
      blockedUserIds: [],
      mailboxDirectory: { 'mailbox-abc': { isGroup: false, userId: 2 } as never },
    })
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      { id: 7, client_uuid: 'echte-nachricht', ciphertext_envelope: 'y', created_at: '' },
    ] as never)

    await checkAndDispatchPendingDeliveryReceipts(1)
    expect(vi.mocked(socialApi.relayE2eeEnvelope)).toHaveBeenCalledTimes(1)

    // So sieht ein Neuladen für diesen Dienst aus: die Sitzungsablage ist
    // weg, `localStorage` bleibt.
    deliveredEnvelopeIds.clear()
    await checkAndDispatchPendingDeliveryReceipts(1)
    expect(vi.mocked(socialApi.relayE2eeEnvelope)).toHaveBeenCalledTimes(1)
  })

  it('quittiert eine neuere Nachricht auch nach dem Neuladen', async () => {
    // Die Kehrseite: der Merker darf nicht verstummen lassen, was wirklich
    // neu ist.
    useMessengerNotificationStore.setState({
      blockedUserIds: [],
      mailboxDirectory: { 'mailbox-abc': { isGroup: false, userId: 2 } as never },
    })
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      { id: 7, client_uuid: 'echte-nachricht', ciphertext_envelope: 'y', created_at: '' },
    ] as never)
    await checkAndDispatchPendingDeliveryReceipts(1)

    deliveredEnvelopeIds.clear()
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      { id: 7, client_uuid: 'echte-nachricht', ciphertext_envelope: 'y', created_at: '' },
      { id: 8, client_uuid: 'noch-eine', ciphertext_envelope: 'z', created_at: '' },
    ] as never)
    await checkAndDispatchPendingDeliveryReceipts(1)

    expect(vi.mocked(socialApi.relayE2eeEnvelope)).toHaveBeenCalledTimes(2)
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

  it('sendet keine Quittung, wenn der Empfänger mit keinem Gerät angemeldet ist', async () => {
    // Früher fiel der Dienst hier auf `encryptE2eeMessage` zurück. Dessen
    // Schlüssel ergibt sich allein aus den beiden Benutzerkennungen, die das
    // Backend beim Relais ohnehin kennt — die Quittung wäre für den Server
    // lesbar gewesen. Ein fehlendes graues Häkchen ist der bessere Preis.
    vi.mocked(e2eeGeraet.geraeteVon).mockResolvedValueOnce([])

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

  it('sendet keine Quittung, wenn der Schlüssel dieses Geräts nicht bereitsteht', async () => {
    // Ohne eigenen Schlüssel gäbe es nichts, womit dieses Gerät den Umschlag
    // für den Absender mitversiegeln könnte.
    vi.mocked(e2eeGeraet.eigenesGeraet).mockRejectedValueOnce(
      new Error('IndexedDB nicht verfügbar')
    )

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
