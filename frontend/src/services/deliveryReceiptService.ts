/**
 * E2EE Delivery Receipt Service
 *
 * Automatically dispatches delivery receipts (Zustellbestaetigung, 2 graue Haeckchen)
 * as soon as an incoming message arrives at the user's device via SSE or background sync,
 * before the user has necessarily opened the chat.
 */

import { relayE2eeEnvelope, getE2eePublicKey, fetchE2eeEnvelopes } from '@/api/social'
import {
  getLocalKeyPair,
  getOrGenerateLocalKeyPair,
  encryptE2eeHybrid,
  encryptE2eeMessage,
} from '@/services/e2eeCrypto'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

// Cache acknowledged envelope IDs in memory to avoid duplicate receipt storms
export const deliveredEnvelopeIds = new Set<number>()
const recipientPublicKeyCache = new Map<number, { key: string | null; fetchedAt: number }>()

export function resetDeliveredEnvelopeCache(): void {
  deliveredEnvelopeIds.clear()
  recipientPublicKeyCache.clear()
}

export async function sendE2eeDeliveryReceipt({
  blindMailboxId,
  envelopeId,
  senderUserId,
  currentUserId,
}: {
  blindMailboxId: string
  envelopeId: number
  senderUserId: number
  currentUserId: number
}): Promise<boolean> {
  if (!blindMailboxId || !envelopeId || !senderUserId || !currentUserId) {
    return false
  }
  if (senderUserId === currentUserId) {
    return false
  }

  // Idempotency: skip if this envelope was already acknowledged on this client
  if (deliveredEnvelopeIds.has(envelopeId)) {
    return false
  }

  // Group chat protection: direct delivery receipts are only valid for 1-on-1 direct chats
  const store = useMessengerNotificationStore.getState()
  const mailboxMeta = store.mailboxDirectory[blindMailboxId]
  if (mailboxMeta?.isGroup) {
    return false
  }

  // Victim Protection: never send delivery receipts to blocked users
  if (store.isBlocked(senderUserId)) {
    return false
  }

  deliveredEnvelopeIds.add(envelopeId)

  try {
    const clientUuid =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : 'deliv-' + Date.now() + '-' + Math.random().toString(36).substring(2, 9)

    const payloadObj = {
      type: 'delivery_receipt',
      delivered_up_to_id: envelopeId,
      receiver_id: currentUserId,
      timestamp: new Date().toISOString(),
      client_uuid: clientUuid,
    }
    const payload = JSON.stringify(payloadObj)

    let localKeyPair = await getLocalKeyPair(currentUserId)
    if (!localKeyPair) {
      localKeyPair = await getOrGenerateLocalKeyPair(currentUserId)
    }

    // Cache public keys for 10 minutes to minimize network latency on delivery acknowledgments
    let recipientPubKey: string | null = null
    const cachedPubKey = recipientPublicKeyCache.get(senderUserId)
    const now = Date.now()
    if (cachedPubKey && now - cachedPubKey.fetchedAt < 600000) {
      recipientPubKey = cachedPubKey.key
    } else {
      try {
        const info = await getE2eePublicKey(senderUserId)
        recipientPubKey = info?.public_key || null
        recipientPublicKeyCache.set(senderUserId, { key: recipientPubKey, fetchedAt: now })
      } catch {
        recipientPubKey = null
      }
    }

    let ciphertext: string
    if (recipientPubKey && localKeyPair?.publicKeyJwk) {
      ciphertext = await encryptE2eeHybrid(payload, recipientPubKey, localKeyPair.publicKeyJwk)
    } else {
      ciphertext = await encryptE2eeMessage(payload, currentUserId, senderUserId)
    }

    await relayE2eeEnvelope({
      blind_mailbox_id: blindMailboxId,
      ciphertext_envelope: ciphertext,
      recipient_id: senderUserId,
      client_uuid: clientUuid,
      is_control: true,
      control_type: 'delivery_receipt',
    })

    return true
  } catch {
    // If delivery failed, remove from cache so subsequent events/polls can retry
    deliveredEnvelopeIds.delete(envelopeId)
    return false
  }
}

/**
 * Scans active direct chats for unacknowledged envelopes that arrived while offline,
 * ensuring the sender sees 2 gray checkmarks as soon as the recipient comes online
 * even without opening the specific conversation.
 */
export async function checkAndDispatchPendingDeliveryReceipts(currentUserId: number): Promise<void> {
  if (!currentUserId) return
  const store = useMessengerNotificationStore.getState()
  const directory = store.mailboxDirectory
  const directEntries = Object.entries(directory).filter(
    ([_, meta]) => !meta.isGroup && meta.userId && !store.isBlocked(meta.userId)
  )

  for (const [mid, meta] of directEntries.slice(0, 15)) {
    try {
      const envelopes = await fetchE2eeEnvelopes(mid)
      for (const env of envelopes) {
        if (env.id && !deliveredEnvelopeIds.has(env.id) && meta.userId) {
          await sendE2eeDeliveryReceipt({
            blindMailboxId: mid,
            envelopeId: env.id,
            senderUserId: meta.userId,
            currentUserId,
          })
        }
      }
    } catch {
      // Non-fatal background sync
    }
  }
}
