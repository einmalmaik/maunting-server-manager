/**
 * Eine Sprachnachricht aufnehmen: Mikrofon, Zeitzähler und das fertige Stück.
 *
 * Gesendet wird hier nichts. Die fertige Aufnahme geht an `onFertig`, und die
 * Seite verschickt sie wie jede andere Nachricht. Bis 09/2026 stand das in
 * `Messenger.tsx`.
 */

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { sendTypingSignal } from '@/api/social'
import type { AudioAttachment } from '@/components/social/ChatMediaAttachments'
import { getAudioTrackConstraints } from '@/lib/audioSettings'
import { toast } from '@/stores/toastStore'

function getSupportedAudioMimeType(): string {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) {
    return ''
  }
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
    'audio/aac',
  ]
  return candidates.find((c) => MediaRecorder.isTypeSupported(c)) || ''
}

interface SprachaufnahmeOptionen {
  /** Dorthin geht der Hinweis „nimmt auf"; leer, solange kein Gespräch offen ist. */
  blindMailboxId: string
  onFertig: (audio: AudioAttachment) => void
}

export function useSprachaufnahme({ blindMailboxId, onFertig }: SprachaufnahmeOptionen) {
  const { t } = useTranslation()
  const [laeuft, setLaeuft] = useState(false)
  const [dauer, setDauer] = useState(0)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const timerIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Wer den Messenger verlässt, gibt das Mikrofon frei.
  useEffect(() => {
    return () => {
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current)
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  const starte = async () => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      toast.error(t('messenger.micUnavailable'))
      return
    }

    try {
      const constraints = getAudioTrackConstraints()
      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: constraints })
      } catch {
        // Fallback without exact deviceId if preferred mic is unavailable
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        })
      }
      mediaStreamRef.current = stream
      audioChunksRef.current = []

      const mimeType = getSupportedAudioMimeType()
      const options = mimeType ? { mimeType } : undefined
      const recorder = new MediaRecorder(stream, options)

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data)
        }
      }

      recorder.start(100)
      mediaRecorderRef.current = recorder
      setLaeuft(true)
      setDauer(0)

      if (blindMailboxId) {
        void sendTypingSignal({
          blind_mailbox_id: blindMailboxId,
          status: 'recording',
        }).catch(() => {})
      }

      timerIntervalRef.current = setInterval(() => {
        setDauer((prev) => prev + 1)
      }, 1000)
    } catch {
      toast.error(t('messenger.micDenied'))
    }
  }

  /** Beendet die Aufnahme; mit `senden` geht sie an `onFertig`, sonst wird sie verworfen. */
  const beende = (senden: boolean) => {
    if (blindMailboxId) {
      void sendTypingSignal({
        blind_mailbox_id: blindMailboxId,
        status: 'idle',
      }).catch(() => {})
    }

    if (timerIntervalRef.current) {
      clearInterval(timerIntervalRef.current)
      timerIntervalRef.current = null
    }

    const sekunden = dauer
    setLaeuft(false)
    setDauer(0)

    const recorder = mediaRecorderRef.current
    const stream = mediaStreamRef.current

    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = () => {
        stream?.getTracks().forEach((t) => t.stop())
        mediaStreamRef.current = null
        mediaRecorderRef.current = null

        if (senden && audioChunksRef.current.length > 0) {
          const mime = recorder.mimeType || getSupportedAudioMimeType() || 'audio/webm'
          const audioBlob = new Blob(audioChunksRef.current, { type: mime })
          const reader = new FileReader()
          reader.onload = () => {
            const dataUrl = reader.result as string
            if (dataUrl) {
              onFertig({
                dataUrl,
                durationSeconds: Math.max(1, sekunden),
                mimeType: mime,
              })
            }
          }
          reader.readAsDataURL(audioBlob)
        }
        audioChunksRef.current = []
      }
      recorder.stop()
    } else {
      stream?.getTracks().forEach((t) => t.stop())
      mediaStreamRef.current = null
      mediaRecorderRef.current = null
      audioChunksRef.current = []
    }
  }

  return {
    laeuft,
    dauer,
    /** Für die Pegelanzeige; gesetzt, bevor `laeuft` wahr wird. */
    stream: mediaStreamRef.current,
    starte,
    beende,
  }
}
