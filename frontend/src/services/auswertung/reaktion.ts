/** Reaktionen auf Nachrichten. */

import type { Lesung } from '@/hooks/useKonversation'
import type { Sammeltafel } from '../nachrichtBezug'
import type { RohReaktion } from '../reaktionen'
import type { UrheberVon } from './typen'

export async function verarbeiteReaktion(
  parsed: Record<string, any>,
  umschlag: Lesung['env'],
  urheberVon: UrheberVon,
  reaktionen: Sammeltafel<RohReaktion>,
): Promise<boolean> {
  if (parsed.type !== 'reaction') return false
  const urheber = await urheberVon(parsed.actor_id)
  if (urheber === null) {
    console.warn('[Messenger] Dropping reaction with forged actor_id:', parsed.actor_id)
    return true
  }
  const zeichen = String(parsed.emoji || '')
  const wer = Number(urheber || 0)
  if (zeichen && wer) {
    reaktionen.ergaenze(parsed, {
      emoji: zeichen,
      actorId: wer,
      nehmen: parsed.aktion === 'nehmen',
      zeitpunkt: String(parsed.zeitpunkt || umschlag.created_at),
    })
  }
  return true
}
