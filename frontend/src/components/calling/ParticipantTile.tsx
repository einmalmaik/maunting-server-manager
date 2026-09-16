import React, { useEffect, useRef } from 'react'
import { Mic, MicOff, Video, VideoOff } from 'lucide-react'
import { apiUrl } from '@/config/api'
import type { Track } from 'livekit-client'
import type { CallParticipant } from '@/stores/useCallStore'

export interface ParticipantTileProps {
  participant: CallParticipant
  /** Kompakte Darstellung für die Teilnehmerleiste statt der Bühne. */
  compact?: boolean
}

/** Hängt eine LiveKit-Spur an ein `<video>` und räumt beim Wechsel auf. */
function useSpur(track: Track | null) {
  const ref = useRef<HTMLVideoElement | null>(null)
  useEffect(() => {
    const element = ref.current
    if (!element || !track) return
    track.attach(element)
    return () => {
      track.detach(element)
    }
  }, [track])
  return ref
}

export const ParticipantTile: React.FC<ParticipantTileProps> = ({ participant, compact = false }) => {
  const videoRef = useSpur(participant.videoTrack)
  const zeigtVideo = Boolean(participant.videoTrack) && !participant.isCameraOff

  // Der grüne Ring ist die Sprechanzeige. Er sitzt bewusst am Rahmen der
  // ganzen Kachel und nicht nur am Avatar: in der Videoansicht gibt es keinen
  // Avatar, und die Anzeige muss in beiden Zuständen dieselbe sein.
  const ring = participant.isSpeaking
    ? 'ring-2 ring-emerald-400 shadow-[0_0_18px_-2px_rgba(52,211,153,0.6)]'
    : 'ring-1 ring-white/10'

  if (compact) {
    return (
      <div
        className={`flex items-center gap-2.5 rounded-2xl bg-slate-950/50 px-2.5 py-2 transition-shadow ${ring}`}
      >
        <Avatar participant={participant} groesse="h-9 w-9" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-white/90">
            {participant.username}
            {participant.isSelf && <span className="text-white/45"> (du)</span>}
          </div>
          <div className="text-[10px] text-white/50">
            {participant.isPending
              ? 'Verbindet…'
              : participant.isMuted
                ? 'Stumm'
                : participant.isSpeaking
                  ? 'Spricht'
                  : 'Dabei'}
          </div>
        </div>
        <div className="flex items-center gap-1.5 text-white/65">
          {participant.isMuted ? (
            <MicOff className="h-3.5 w-3.5 text-rose-300" aria-label="Mikrofon aus" />
          ) : (
            <Mic className="h-3.5 w-3.5" aria-label="Mikrofon an" />
          )}
          {participant.isCameraOff ? (
            <VideoOff className="h-3.5 w-3.5" aria-label="Kamera aus" />
          ) : (
            <Video className="h-3.5 w-3.5 text-cyan-300" aria-label="Kamera an" />
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      className={`relative flex min-h-[9rem] items-center justify-center overflow-hidden rounded-2xl bg-slate-900 transition-shadow ${ring}`}
    >
      {zeigtVideo ? (
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted={participant.isSelf}
          className={`h-full w-full object-cover ${participant.isSelf ? '-scale-x-100' : ''}`}
        />
      ) : (
        <Avatar participant={participant} groesse="h-20 w-20 sm:h-24 sm:w-24" />
      )}

      <div className="absolute inset-x-2 bottom-2 flex items-center justify-between gap-2">
        <span className="max-w-[70%] truncate rounded-full bg-slate-950/70 px-2 py-0.5 text-[11px] font-medium text-white/90">
          {participant.username}
          {participant.isSelf && <span className="text-white/45"> (du)</span>}
        </span>
        <span className="flex items-center gap-1 rounded-full bg-slate-950/70 px-2 py-0.5 text-white/75">
          {participant.isMuted ? (
            <MicOff className="h-3.5 w-3.5 text-rose-300" aria-label="Mikrofon aus" />
          ) : (
            <Mic className="h-3.5 w-3.5" aria-label="Mikrofon an" />
          )}
        </span>
      </div>

      {participant.isPending && (
        <span className="absolute left-2 top-2 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] uppercase tracking-wider text-amber-100">
          Verbindet
        </span>
      )}
    </div>
  )
}

const Avatar: React.FC<{ participant: CallParticipant; groesse: string }> = ({
  participant,
  groesse,
}) => {
  const [kaputt, setKaputt] = React.useState(false)
  React.useEffect(() => setKaputt(false), [participant.avatarUrl])

  return (
    <div
      className={`${groesse} shrink-0 overflow-hidden rounded-full border border-white/15 bg-slate-700`}
    >
      {participant.avatarUrl && !kaputt ? (
        <img
          src={apiUrl(participant.avatarUrl)}
          alt={participant.username}
          className="h-full w-full object-cover"
          onError={() => setKaputt(true)}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-sm font-semibold text-white/80">
          {participant.username.slice(0, 2).toUpperCase()}
        </div>
      )}
    </div>
  )
}
