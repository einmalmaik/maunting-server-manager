import React, { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Play, Pause, Volume2, VolumeX, Maximize2, Loader2 } from 'lucide-react'
import {
  holeAnhangUrl,
  type MedienBindungsKontext,
  type VideoNoteAttachment,
} from '@/components/social/ChatMediaAttachments'

export interface CircularVideoNotePlayerProps {
  attachment: VideoNoteAttachment
  bindung: MedienBindungsKontext
  /** Die lokale Blob-URL des Absenders, damit seine eigene Aufnahme sofort läuft. */
  videoUrl?: string
  onExpand?: () => void
}

export const CircularVideoNotePlayer: React.FC<CircularVideoNotePlayerProps> = ({
  attachment,
  bindung,
  videoUrl,
  onExpand,
}) => {
  const { t } = useTranslation()

  const [isPlaying, setIsPlaying] = useState(true)
  const [isMuted, setIsMuted] = useState(true)
  const [quelle, setQuelle] = useState<string | null>(videoUrl || null)
  const videoRef = useRef<HTMLVideoElement | null>(null)

  // Einzelwerte in der Abhängigkeitsliste: `attachment` und `bindung` sind bei
  // jedem Rendern neue Objekte und trieben den Effekt sonst in eine Schleife.
  const { mediaId, paketSchluessel, fileId } = attachment
  const { absenderId, blindMailboxId } = bindung

  // Ohne lokale Aufnahme kommt das Video aus dem Medienspeicher. Vorher stand
  // hier die Blob-URL des Absenders, die beim Empfänger ins Leere zeigte.
  useEffect(() => {
    if (videoUrl) {
      setQuelle(videoUrl)
      return
    }
    let aktiv = true
    void holeAnhangUrl({ mediaId, paketSchluessel, fileId }, { absenderId, blindMailboxId }).then(
      (url) => {
        if (aktiv) setQuelle(url)
      }
    )
    return () => {
      aktiv = false
    }
  }, [videoUrl, mediaId, paketSchluessel, fileId, absenderId, blindMailboxId])

  const togglePlay = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!videoRef.current) return
    if (isPlaying) {
      videoRef.current.pause()
      setIsPlaying(false)
    } else {
      videoRef.current.play()
      setIsPlaying(true)
    }
  }

  const toggleMute = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!videoRef.current) return
    const nextMuted = !isMuted
    videoRef.current.muted = nextMuted
    setIsMuted(nextMuted)
  }

  return (
    <div
      onClick={onExpand}
      className="relative w-44 h-44 sm:w-52 sm:h-52 rounded-full overflow-hidden bg-surface-container-high border-2 border-primary/40 shadow-lg cursor-pointer group select-none transition-transform hover:scale-[1.02]"
      title={t('social.videoNote.expand')}
    >
      {quelle ? (
        <video
          ref={videoRef}
          src={quelle}
          autoPlay
          loop
          playsInline
          muted={isMuted}
          className="w-full h-full object-cover"
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-white/60" />
        </div>
      )}

      {/* Floating control buttons */}
      <div className="absolute inset-0 bg-black/20 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-3">
        <button
          type="button"
          onClick={togglePlay}
          className="w-9 h-9 rounded-full bg-black/60 hover:bg-black/80 text-white flex items-center justify-center backdrop-blur-sm transition-transform hover:scale-110"
        >
          {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
        </button>

        <button
          type="button"
          onClick={toggleMute}
          className="w-9 h-9 rounded-full bg-black/60 hover:bg-black/80 text-white flex items-center justify-center backdrop-blur-sm transition-transform hover:scale-110"
        >
          {isMuted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
        </button>

        {onExpand && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onExpand()
            }}
            className="w-9 h-9 rounded-full bg-black/60 hover:bg-black/80 text-white flex items-center justify-center backdrop-blur-sm transition-transform hover:scale-110"
          >
            <Maximize2 className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Duration Badge */}
      <div className="absolute bottom-2 left-1/2 -translate-x-1/2 bg-black/60 px-2 py-0.5 rounded-full text-[10px] font-mono text-white/90">
        {attachment.durationSeconds}s
      </div>
    </div>
  )
}
