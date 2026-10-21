/** Verfallsfrist aus Steuerpaketen. */

import type { ChatGroupItem } from '@/api/social'
import type { Lesung } from '@/hooks/useKonversation'
import { durfteVerfallStellen, istBekannteStufe, stufenDativ, uebernehmeVerfall } from '../nachrichtVerfall'
import type { UrheberVon } from './typen'

export interface VerfallsKontext {
  currentMid: string
  currentUserId: number
  activeContact: { userId: number; username: string } | null
  activeGroup: ChatGroupItem | null
  t: (schluessel: string, werte?: Record<string, unknown>) => string
  setVerfallSekunden: (sekunden: number) => void
  zeigeSystemzeile: (text: string) => void
}

export async function verarbeiteVerfall(
  parsed: Record<string, any>,
  umschlag: Lesung['env'],
  urheberVon: UrheberVon,
  kontext: VerfallsKontext,
): Promise<boolean> {
  if (parsed.type !== 'retention') return false

  const {
    currentMid,
    currentUserId,
    activeContact,
    activeGroup,
    t,
    setVerfallSekunden,
    zeigeSystemzeile,
  } = kontext

  const urheber = await urheberVon(parsed.actor_id)
  if (urheber === null) {
    console.warn('[Messenger] Dropping retention packet with forged actor_id:', parsed.actor_id)
    return true
  }
  const wer = Number(urheber || 0)
  if (activeGroup && !(wer && durfteVerfallStellen(activeGroup, wer))) return true
  const dauer = Number(parsed.dauer || 0)
  const wann = String(parsed.zeitpunkt || umschlag.created_at)
  if (istBekannteStufe(dauer) && uebernehmeVerfall(currentMid, dauer, wann)) {
    const selbst = wer === Number(currentUserId)
    const name = selbst
      ? t('messenger.retentionYou')
      : (activeGroup?.members ?? []).find((m) => Number(m.user_id) === wer)?.username ||
        activeContact?.username ||
        t('messenger.retentionOther')
    setVerfallSekunden(dauer)
    zeigeSystemzeile(
      dauer > 0
        ? t(selbst ? 'messenger.retentionSetSelf' : 'messenger.retentionSetOther', {
            name,
            frist: stufenDativ(dauer, t),
          })
        : t(selbst ? 'messenger.retentionOffSelf' : 'messenger.retentionOffOther', { name }),
    )
  }
  return true
}
