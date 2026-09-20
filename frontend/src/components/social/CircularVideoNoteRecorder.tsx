import React, { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X, Check, Lock } from 'lucide-react'
import {
  evaluateSwipeGesture,
  calculateProgressRingOffset,
  MAX_VIDEO_NOTE_DURATION_SEC,
} from '@/lib/videoNotizGesten'
import { toast } from '@/stores/toastStore'

/**
 * Die fertige Aufnahme, noch ohne Anhangdaten.
 *
 * Verschlüsselt und hochgeladen wird erst im Sendepfad: dort steht fest, in
 * welche Mailbox der Anhang gehört, und genau das bindet DIS in die
 * gebundenen Daten. Der Rekorder hat diese Angaben nicht und soll sie auch
 * nicht bekommen — vorher verschlüsselte er hier selbst und warf den Umschlag
 * anschließend weg.
 */
export interface VideoNoteAufnahme {
  blob: Blob
  durationSeconds: number
  width: number
  height: number
  mimeType: string
}

export interface CircularVideoNoteRecorderProps {
  onCancel: () => void
  onComplete: (aufnahme: VideoNoteAufnahme) => void
}

export const CircularVideoNoteRecorder: React.FC<CircularVideoNoteRecorderProps> = ({
  onCancel,
  onComplete,
}) => {
  const { t } = useTranslation()

  const [isLocked, setIsLocked] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [isRecording, setIsRecording] = useState(true)
  /**
   * Führt ein Finger die Aufnahme?
   *
   * Nur am Finger ergibt „Loslassen beendet sie" einen Sinn. Geöffnet wird der
   * Rekorder aber per Klick, und am Rechner gibt es keine Touch-Ereignisse:
   * `isLocked` blieb dort für immer false, die Knöpfe unten wurden nie
   * gerendert und `handleTouchEnd` kam nie. Der einzige Ausweg aus einer
   * versehentlich geöffneten Aufnahme waren die vollen 60 Sekunden. Ohne Geste
   * stehen die Knöpfe deshalb von Anfang an da.
   */
  const [gestengefuehrt, setGestengefuehrt] = useState(false)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  // Touch coordinates
  const startCoordRef = useRef<{ x: number; y: number } | null>(null)
  /** Einmal beenden. Zeitgeber und Knopf können sonst beide zuschlagen. */
  const beendet = useRef(false)

  // Start camera and recording
  useEffect(() => {
    let active = true

    navigator.mediaDevices.getUserMedia({
      audio: true,
      video: {
        facingMode: 'user',
        width: { ideal: 480 },
        height: { ideal: 480 },
      },
    }).then((stream) => {
      if (!active) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }

      const recorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus')
          ? 'video/webm;codecs=vp8,opus'
          : 'video/webm',
      })
      mediaRecorderRef.current = recorder
      chunksRef.current = []

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          chunksRef.current.push(e.data)
        }
      }

      recorder.start(250)
    }).catch(() => {
      toast.error(t('social.videoNote.cameraFailed'))
      onCancel()
    })

    return () => {
      active = false
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop())
      }
    }
  }, [onCancel])

  const handleStopAndFinish = async () => {
    if (beendet.current) return
    beendet.current = true
    setIsRecording(false)
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
    }

    // Wait a brief tick for ondataavailable
    setTimeout(() => {
      onComplete({
        blob: new Blob(chunksRef.current, { type: 'video/webm' }),
        durationSeconds: Math.max(1, elapsedSeconds),
        width: 360,
        height: 360,
        mimeType: 'video/webm',
      })
    }, 200)
  }

  // Duration timer
  useEffect(() => {
    if (!isRecording) return
    const interval = setInterval(() => {
      setElapsedSeconds((prev) => Math.min(prev + 1, MAX_VIDEO_NOTE_DURATION_SEC))
    }, 1000)
    return () => clearInterval(interval)
  }, [isRecording])

  /**
   * Das Zeitlimit beendet die Aufnahme — hier, nicht im Zustandsaktualisierer.
   *
   * Dort stand es bis 09/2026, und das kostete zweimal: ein Aktualisierer muss
   * rein sein, React ruft ihn in der Entwicklung bewusst doppelt auf, und so
   * ging jede auslaufende Notiz **zweimal** raus (zwei Uploads, zwei Zeilen im
   * Verlauf). Ausserdem sah der Intervall-Rückruf `elapsedSeconds` aus dem
   * Render, in dem er entstand — also 0 —, und `Math.max(1, 0)` schrieb jeder
   * so beendeten Notiz eine Dauer von **1 s** zu, egal wie lang sie war.
   */
  useEffect(() => {
    if (elapsedSeconds >= MAX_VIDEO_NOTE_DURATION_SEC) void handleStopAndFinish()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elapsedSeconds])

  // Touch gesture listeners
  const handleTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0]
    startCoordRef.current = { x: t.clientX, y: t.clientY }
    setGestengefuehrt(true)
  }

  const handleTouchMove = (e: React.TouchEvent) => {
    if (!startCoordRef.current || isLocked) return
    const t = e.touches[0]
    const deltaX = t.clientX - startCoordRef.current.x
    const deltaY = t.clientY - startCoordRef.current.y

    const gesture = evaluateSwipeGesture(deltaX, deltaY)
    if (gesture === 'lock_video') {
      setIsLocked(true)
    } else if (gesture === 'cancel') {
      onCancel()
    }
  }

  const handleTouchEnd = () => {
    if (!isLocked) {
      handleStopAndFinish()
    }
  }

  const { circumference, strokeDashoffset } = calculateProgressRingOffset(elapsedSeconds)

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/80 backdrop-blur-md select-none touch-none animate-fade-in"
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      {/* Circular Camera Preview with SVG Progress Ring */}
      <div className="relative w-64 h-64 sm:w-80 sm:h-80 flex items-center justify-center">
        {/* SVG Duration Ring */}
        <svg className="absolute inset-0 w-full h-full -rotate-90 pointer-events-none">
          <circle
            cx="50%"
            cy="50%"
            r="48%"
            fill="none"
            stroke="rgba(255,255,255,0.15)"
            strokeWidth="4"
          />
          <circle
            cx="50%"
            cy="50%"
            r="48%"
            fill="none"
            stroke="#10b981"
            strokeWidth="4"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            className="transition-all duration-1000 ease-linear"
          />
        </svg>

        {/* Circular Video Container */}
        <div className="w-[92%] h-[92%] rounded-full overflow-hidden border-2 border-status-success/50 bg-surface-container-high shadow-2xl relative">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full h-full object-cover -scale-x-100"
          />
          <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-black/60 px-2.5 py-0.5 rounded-full text-xs font-mono text-white flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-status-destructive animate-pulse" />
            <span>{elapsedSeconds}s / {MAX_VIDEO_NOTE_DURATION_SEC}s</span>
          </div>

          {!isLocked && gestengefuehrt && (
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-black/60 px-3 py-1 rounded-full text-[11px] text-white/80 flex items-center gap-1 animate-bounce">
              <Lock className="w-3 h-3 text-status-success" />
              <span>{t('social.videoNote.swipeToLock')}</span>
            </div>
          )}
        </div>
      </div>

      {/* Bedienknöpfe: gesperrt, oder wenn keine Geste die Aufnahme führt. */}
      {(isLocked || !gestengefuehrt) && (
        <div className="mt-8 flex items-center gap-6">
          <button
            type="button"
            onClick={onCancel}
            className="w-12 h-12 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-transform hover:scale-105"
            title={t('common.cancel')}
          >
            <X className="w-6 h-6" />
          </button>
          <button
            type="button"
            onClick={handleStopAndFinish}
            className="w-14 h-14 rounded-full bg-status-success hover:bg-status-success/90 text-on-surface flex items-center justify-center shadow-lg shadow-status-success/30 transition-transform hover:scale-105"
            title={t('social.videoNote.send')}
          >
            <Check className="w-7 h-7" />
          </button>
        </div>
      )}
    </div>
  )
}
