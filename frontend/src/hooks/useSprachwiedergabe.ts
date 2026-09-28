/**
 * Die Wiedergabe von Sprachnachrichten, gemeinsam für alle Blasen eines Chats.
 *
 * Es spielt höchstens eine Aufnahme; wer eine andere startet, hält die laufende
 * an. Bis 09/2026 stand das in `Messenger.tsx`, der Anhalte-Block viermal.
 */

import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { useTranslation } from 'react-i18next'

import {
  holeAnhangUrl,
  type AudioAttachment,
  type MedienBindungsKontext,
} from '@/components/social/ChatMediaAttachments'
import type { TonZustand } from '@/components/social/ChatMessageBubble'
import { toast } from '@/stores/toastStore'

const TEMPI = [1, 1.5, 2]

function halteAn(laufend: MutableRefObject<HTMLAudioElement | null>) {
  const audio = laufend.current
  if (!audio) return
  audio.pause()
  audio.ontimeupdate = null
  audio.onended = null
  audio.onerror = null
  laufend.current = null
}

export function useSprachwiedergabe(): TonZustand {
  const { t } = useTranslation()
  const [playingAudioId, setPlayingAudioId] = useState<number | null>(null)
  const [audioCurrentTime, setAudioCurrentTime] = useState(0)
  const [audioPlaybackRate, setAudioPlaybackRate] = useState(1)
  const laufend = useRef<HTMLAudioElement | null>(null)

  // Wer den Messenger verlässt, lässt nichts weiterspielen.
  useEffect(() => () => halteAn(laufend), [])

  /** Hält die laufende Aufnahme an und spielt diese ab `start` Sekunden. */
  const spiele = async (
    messageId: number,
    anhang: AudioAttachment,
    bindung: MedienBindungsKontext,
    start: number,
  ) => {
    halteAn(laufend)
    // Die eigene Aufnahme, solange sie lokal liegt; sonst aus dem Medienspeicher.
    const quelle = anhang.dataUrl || (await holeAnhangUrl(anhang, bindung))
    if (!quelle) {
      toast.error(t('messenger.voiceLoadFailed'))
      return
    }

    const audio = new Audio(quelle)
    audio.playbackRate = audioPlaybackRate
    if (start > 0) audio.currentTime = start
    laufend.current = audio
    setPlayingAudioId(messageId)
    setAudioCurrentTime(start)

    audio.ontimeupdate = () => setAudioCurrentTime(audio.currentTime)
    audio.onended = () => {
      setPlayingAudioId(null)
      setAudioCurrentTime(0)
      laufend.current = null
    }
    audio.onerror = () => {
      setPlayingAudioId(null)
      toast.error(t('messenger.voicePlayFailed'))
    }
    audio.play().catch(() => setPlayingAudioId(null))
  }

  const onTogglePlay: TonZustand['onTogglePlay'] = (messageId, anhang, bindung) => {
    if (playingAudioId === messageId) {
      halteAn(laufend)
      setPlayingAudioId(null)
      return
    }
    void spiele(messageId, anhang, bindung, 0)
  }

  // Wechselt zwischen 1-, 1,5- und 2-facher Geschwindigkeit.
  const onCycleRate: TonZustand['onCycleRate'] = (e) => {
    e.stopPropagation()
    const naechstes = TEMPI[(TEMPI.indexOf(audioPlaybackRate) + 1) % TEMPI.length] || 1
    setAudioPlaybackRate(naechstes)
    if (laufend.current) laufend.current.playbackRate = naechstes
  }

  // Ein Klick irgendwo auf die Wellenform springt an diese Stelle.
  const onSeek: TonZustand['onSeek'] = (messageId, anhang, bindung, e) => {
    e.stopPropagation()
    // Die Maße müssen vor jedem `await` feststehen: React gibt das Ereignis
    // danach frei und `currentTarget` ist null.
    const rect = e.currentTarget.getBoundingClientRect()
    const dauer = anhang.durationSeconds
    if (rect.width <= 0 || dauer <= 0) return
    const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width))
    const ziel = (x / rect.width) * dauer

    if (playingAudioId === messageId && laufend.current) {
      laufend.current.currentTime = ziel
      setAudioCurrentTime(ziel)
      return
    }
    void spiele(messageId, anhang, bindung, ziel)
  }

  return { playingAudioId, audioCurrentTime, audioPlaybackRate, onTogglePlay, onCycleRate, onSeek }
}
