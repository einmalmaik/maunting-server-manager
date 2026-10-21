/** Angeheftete Gruppennachrichten. */

import type { ChatGroupItem } from '@/api/social'
import type { Lesung } from '@/hooks/useKonversation'
import { durfteAnheften, uebernehmeAnheftung } from '../nachrichtAnheftung'
import type { UrheberVon } from './typen'

export async function verarbeiteAnheftung(
  parsed: Record<string, any>,
  umschlag: Lesung['env'],
  urheberVon: UrheberVon,
  activeGroup: ChatGroupItem | null,
  currentMid: string,
): Promise<boolean> {
  if (parsed.type !== 'pin_message') return false
  const urheber = await urheberVon(parsed.actor_id)
  if (urheber === null) {
    console.warn('[Messenger] Dropping pin packet with forged actor_id:', parsed.actor_id)
    return true
  }
  const wer = Number(urheber || 0)
  const ziel = String(parsed.target_client_uuid || '')
  const wann = String(parsed.zeitpunkt || umschlag.created_at)
  const geloest = parsed.aktion === 'loesen'
  if (wer && durfteAnheften(activeGroup, wer)) {
    uebernehmeAnheftung(currentMid, geloest ? '' : ziel, wann)
  }
  return true
}
