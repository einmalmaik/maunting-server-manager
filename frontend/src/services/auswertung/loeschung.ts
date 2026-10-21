/** Löschen-Steuerpakete. */

import type { Lesung } from '@/hooks/useKonversation'
import type { Bezugstafel } from '../nachrichtBezug'
import type { UrheberVon } from './typen'

export async function verarbeiteLoeschung(
  parsed: Record<string, any>,
  umschlag: Lesung['env'],
  urheberVon: UrheberVon,
  loeschungen: Bezugstafel<{ deletedAt: string }>,
): Promise<boolean> {
  if (parsed.type !== 'delete_message') return false
  const urheber = await urheberVon(parsed.actor_id ?? parsed.sender_id)
  if (urheber === null) {
    console.warn('[Messenger] Dropping delete packet with forged actor_id:', parsed.actor_id)
    return true
  }
  loeschungen.merke(
    parsed,
    {
      deletedAt: String(parsed.deleted_at || umschlag.created_at),
    },
    urheber,
  )
  return true
}
