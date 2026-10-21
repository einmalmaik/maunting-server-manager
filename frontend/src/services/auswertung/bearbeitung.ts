/** Bearbeiten-Steuerpakete. */

import type { Lesung } from '@/hooks/useKonversation'
import type { Bezugstafel } from '../nachrichtBezug'
import type { UrheberVon } from './typen'

export async function verarbeiteBearbeitung(
  parsed: Record<string, any>,
  umschlag: Lesung['env'],
  urheberVon: UrheberVon,
  aenderungen: Bezugstafel<{ newText: string; editedAt: string }>,
): Promise<boolean> {
  if (parsed.type !== 'edit_message') return false
  if (parsed.new_text) {
    const urheber = await urheberVon(parsed.actor_id ?? parsed.sender_id)
    if (urheber === null) {
      console.warn('[Messenger] Dropping edit packet with forged actor_id:', parsed.actor_id)
      return true
    }
    aenderungen.merke(
      parsed,
      {
        newText: String(parsed.new_text),
        editedAt: String(parsed.edited_at || umschlag.created_at),
      },
      urheber,
    )
  }
  return true
}
