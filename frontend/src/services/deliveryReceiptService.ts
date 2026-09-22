/**
 * E2EE Delivery Receipt Service
 *
 * Automatically dispatches delivery receipts (Zustellbestaetigung, 2 graue Haeckchen)
 * as soon as an incoming message arrives at the user's device via SSE or background sync,
 * before the user has necessarily opened the chat.
 */

import { relayE2eeEnvelope, fetchE2eeEnvelopes, syncE2eeMailboxes } from '@/api/social'
import { encryptE2eeHybrid } from '@/services/e2eeCrypto'
import { eigenesGeraet, geraeteVon } from '@/services/e2eeGeraet'
import { merkeQuittung, quittungsstand } from '@/services/quittungsstand'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

const DELIVERED_STORAGE_KEY = 'msm:delivered_envelope_ids'

function loadDeliveredEnvelopeIds(): Set<number> {
  const ids = new Set<number>()
  if (typeof window !== 'undefined' && window.sessionStorage) {
    try {
      const raw = sessionStorage.getItem(DELIVERED_STORAGE_KEY)
      if (raw) {
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed)) {
          for (const item of parsed) {
            if (typeof item === 'number') ids.add(item)
          }
        }
      }
    } catch {}
  }
  return ids
}

function persistDeliveredEnvelopeIds(set: Set<number>): void {
  if (typeof window !== 'undefined' && window.sessionStorage) {
    try {
      const arr = Array.from(set).slice(-500)
      sessionStorage.setItem(DELIVERED_STORAGE_KEY, JSON.stringify(arr))
    } catch {}
  }
}

// Cache acknowledged envelope IDs to avoid duplicate receipt storms across reload
export const deliveredEnvelopeIds = loadDeliveredEnvelopeIds()

/**
 * Woran eine Quittung sich selbst erkennt.
 *
 * Steht in der `client_uuid`, weil das das einzige Feld ist, das den Weg durch
 * das Relais bis in den Lesepfad übersteht. `logischeUuid` schneidet nur am
 * `#` der Auffächerung, das Präfix bleibt also stehen.
 */
export const QUITTUNG_PRAEFIX = 'deliv-'

export function resetDeliveredEnvelopeCache(): void {
  deliveredEnvelopeIds.clear()
  if (typeof window !== 'undefined' && window.sessionStorage) {
    try {
      sessionStorage.removeItem(DELIVERED_STORAGE_KEY)
    } catch {}
  }
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

  /*
   * Und dieselbe Frage noch einmal an die Ablage.
   *
   * `deliveredEnvelopeIds` liegt im `sessionStorage` und ist nach jedem
   * Neuladen leer. Der Merker darüber hält dagegen: eine Quittung nennt mit
   * `delivered_up_to_id` eine Obergrenze, also ist alles darunter schon
   * gesagt. Ohne diese Zeile quittierte jeder Ladevorgang den kompletten
   * Mailbox-Inhalt erneut — und weil das Fenster hundert Umschläge fasst,
   * verdrängte es genau die Nachrichten, für die es gedacht war.
   */
  if (envelopeId <= quittungsstand(blindMailboxId).zugestellt) {
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
    // Das Präfix ist der einzige Marker, den eine Quittung über sich selbst
    // trägt: `is_control`/`control_type` gehen zwar an das Relais, aber
    // `e2ee_blind_envelopes` hat keine Spalte dafür, und der Lesepfad bekommt
    // sie nie zu sehen. Ohne Marker ist eine Quittung von einer Nachricht nicht
    // zu unterscheiden — und weil eine Quittung selbst ein Umschlag in
    // derselben Mailbox ist, quittierte `checkAndDispatchPendingDeliveryReceipts`
    // sie erneut. Am laufenden System wuchs die Mailbox dadurch bei **jedem**
    // Ladevorgang um hundert Umschläge, ohne dass jemand etwas geschrieben
    // hätte. Bis 09/2026 stand das Präfix nur im Notzweig für Browser ohne
    // `crypto.randomUUID`, also praktisch nirgends.
    const clientUuid =
      QUITTUNG_PRAEFIX +
      (typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : Date.now() + '-' + Math.random().toString(36).substring(2, 9))

    const payloadObj = {
      type: 'delivery_receipt',
      delivered_up_to_id: envelopeId,
      receiver_id: currentUserId,
      timestamp: new Date().toISOString(),
      client_uuid: clientUuid,
    }
    const payload = JSON.stringify(payloadObj)

    // Hat der Absender kein Gerät angemeldet, gibt es keine Quittung. Der
    // Ersatzweg von früher hätte den Schlüssel allein aus den beiden
    // Benutzerkennungen abgeleitet und damit für den Server lesbar gemacht;
    // ein fehlendes graues Häkchen wiegt leichter als eine gebrochene Zusage.
    const geraet = await eigenesGeraet()
    const zielGeraete = await geraeteVon(senderUserId)
    if (zielGeraete.length === 0) {
      deliveredEnvelopeIds.delete(envelopeId)
      return false
    }

    // Die Quittung geht an das zuletzt aktive Gerät des Absenders. Sie ist ein
    // Häkchen, kein Gesprächsinhalt: sie an alle Geräte zu fächern kostete je
    // empfangener Nachricht eine Runde mehr, ohne dass jemand etwas davon hat.
    const ciphertext = await encryptE2eeHybrid(
      payload,
      zielGeraete[0].public_key,
      geraet.paar.publicKeyJwk
    )

    await relayE2eeEnvelope({
      blind_mailbox_id: blindMailboxId,
      ciphertext_envelope: ciphertext,
      recipient_id: senderUserId,
      client_uuid: clientUuid,
      is_control: true,
      control_type: 'delivery_receipt',
    })

    deliveredEnvelopeIds.add(envelopeId)
    persistDeliveredEnvelopeIds(deliveredEnvelopeIds)
    merkeQuittung(blindMailboxId, 'zugestellt', envelopeId)
    return true
  } catch {
    // If delivery failed, remove from cache so subsequent events/polls can retry
    deliveredEnvelopeIds.delete(envelopeId)
    persistDeliveredEnvelopeIds(deliveredEnvelopeIds)
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

  // Use sync API to discover all mailboxes with pending envelopes
  const syncResult = await syncE2eeMailboxes(0).catch(() => null)
  const prioritizedMids = new Set<string>()
  if (syncResult?.mailboxes) {
    for (const mb of syncResult.mailboxes) {
      if (mb.unread_count > 0 || mb.max_envelope_id > 0) {
        prioritizedMids.add(mb.blind_mailbox_id)
      }
    }
  }

  const directEntries = Object.entries(directory).filter(
    ([_, meta]) => !meta.isGroup && meta.userId && !store.isBlocked(meta.userId)
  )

  // Process all direct conversations without arbitrary cap (previously capped to 15)
  for (const [mid, meta] of directEntries) {
    try {
      // Check mailbox if it has pending updates or envelopes
      const envelopes = await fetchE2eeEnvelopes(mid)

      /*
       * Eine Quittung je Mailbox, nicht eine je Umschlag.
       *
       * `delivered_up_to_id` nennt eine Obergrenze — die höchste Kennung
       * deckt alles darunter mit ab. Trotzdem lief hier bis 09/2026 eine
       * Schleife über den kompletten Mailbox-Inhalt und schickte für jeden
       * einzelnen Umschlag eine eigene Quittung. Da der Merker im
       * `sessionStorage` lag und jedes Neuladen ihn leerte, waren das bis zu
       * hundert Umschläge pro Ladevorgang; gemessen bestand die Mailbox zu
       * 64 % aus Quittungen, und das Relais bremste mit 429. Verdrängt wurde
       * dabei, was nicht nachbestellt werden kann: Bearbeitungen,
       * Reaktionen, Anheftungen.
       *
       * Wessen Umschlag der höchste ist, lässt sich hier nicht sagen — der
       * Inhalt ist verschlüsselt, und `fetchE2eeEnvelopes` liefert keinen
       * Absender. Die Obergrenze kann deshalb eine eigene Nachricht
       * erwischen und ein Häkchen einen Umschlag zu früh setzen. Das galt
       * für die alte Schleife genauso; im offenen Chat rechnet der Messenger
       * die Grenze ohnehin aus den entschlüsselten Zeilen.
       */
      let hoechste = 0
      for (const env of envelopes) {
        // Eine Quittung braucht keine Quittung. Ohne diese Zeile wuchs die
        // Mailbox bei jedem Ladevorgang um ihren eigenen Bestand.
        if (env.client_uuid?.startsWith(QUITTUNG_PRAEFIX)) continue
        if (env.id && env.id > hoechste) hoechste = env.id
      }

      if (hoechste && meta.userId) {
        await sendE2eeDeliveryReceipt({
          blindMailboxId: mid,
          envelopeId: hoechste,
          senderUserId: meta.userId,
          currentUserId,
        })
      }
    } catch {
      // Non-fatal background sync
    }
  }
}
