import React, { useEffect, useRef, useState } from 'react'
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
  const [isLocked, setIsLocked] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [isRecording, setIsRecording] = useState(true)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  // Touch coordinates
  const startCoordRef = useRef<{ x: number; y: number } | null>(null)

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
      toast.error('Kamerazugriff für Videonotiz fehlgeschlagen')
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
      setElapsedSeconds((prev) => {
        if (prev + 1 >= MAX_VIDEO_NOTE_DURATION_SEC) {
          handleStopAndFinish()
          return MAX_VIDEO_NOTE_DURATION_SEC
        }
        return prev + 1
      })
    }, 1000)
    return () => clearInterval(interval)
  }, [isRecording])

  // Touch gesture listeners
  const handleTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0]
    startCoordRef.current = { x: t.clientX, y: t.clientY }
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
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/80 backdrop-blur-md select-none touch-none animate-in fade-in"
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
        <div className="w-[92%] h-[92%] rounded-full overflow-hidden border-2 border-emerald-500/50 bg-slate-900 shadow-2xl relative">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full h-full object-cover -scale-x-100"
          />
          <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-black/60 px-2.5 py-0.5 rounded-full text-xs font-mono text-white flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-rose-500 animate-pulse" />
            <span>{elapsedSeconds}s / {MAX_VIDEO_NOTE_DURATION_SEC}s</span>
          </div>

          {!isLocked && (
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-black/60 px-3 py-1 rounded-full text-[11px] text-white/80 flex items-center gap-1 animate-bounce">
              <Lock className="w-3 h-3 text-emerald-400" />
              <span>Nach oben swipen zum Sperren</span>
            </div>
          )}
        </div>
      </div>

      {/* Locked Controls */}
      {isLocked && (
        <div className="mt-8 flex items-center gap-6">
          <button
            type="button"
            onClick={onCancel}
            className="w-12 h-12 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-transform hover:scale-105"
            title="Abbrechen"
          >
            <X className="w-6 h-6" />
          </button>
          <button
            type="button"
            onClick={handleStopAndFinish}
            className="w-14 h-14 rounded-full bg-emerald-500 hover:bg-emerald-600 text-white flex items-center justify-center shadow-lg shadow-emerald-500/30 transition-transform hover:scale-105"
            title="Absenden"
          >
            <Check className="w-7 h-7" />
          </button>
        </div>
      )}
    </div>
  )
}
