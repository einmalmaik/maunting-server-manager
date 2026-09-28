import React, { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Mic, MicOff, Video, VideoOff, VolumeX } from 'lucide-react'
import { Avatar } from '@/Singra/UI/Avatar'
import type { Track } from 'livekit-client'
import type { CallParticipant } from '@/stores/useCallStore'

export interface ParticipantTileProps {
  participant: CallParticipant
  /** Kompakte Darstellung für die Teilnehmerleiste statt der Bühne. */
  compact?: boolean
  /** Klick auf die Kachel — öffnet das Teilnehmermenü. */
  onSelect?: (participant: CallParticipant) => void
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

type Uebersetzer = (schluessel: string) => string

function zustandstext(participant: CallParticipant, t: Uebersetzer): string {
  if (participant.isPending) return t('calls.connecting')
  if (participant.volume === 0 && !participant.isSelf) return t('calls.mutedForYou')
  if (participant.isMuted) return t('calls.muted')
  if (participant.isSpeaking) return t('calls.speaking')
  return t('calls.present')
}

export const ParticipantTile: React.FC<ParticipantTileProps> = ({
  participant,
  compact = false,
  onSelect,
}) => {
  const { t } = useTranslation()

  const videoRef = useSpur(participant.videoTrack)
  const zeigtVideo = Boolean(participant.videoTrack) && !participant.isCameraOff
  const lokalStumm = participant.volume === 0 && !participant.isSelf

  // Der Ring ist die Sprechanzeige. Er sitzt bewusst am Rahmen der ganzen
  // Kachel und nicht nur am Avatar: in der Videoansicht gibt es keinen Avatar,
  // und die Anzeige muss in beiden Zuständen dieselbe sein.
  const ring = participant.isSpeaking
    ? 'ring-2 ring-status-success shadow-lg shadow-status-success/40'
    : 'ring-1 ring-outline-variant/40'

  const klickbar = Boolean(onSelect)
  const gemeinsam = `w-full min-w-0 text-left transition-shadow ${ring} ${
    klickbar ? 'cursor-pointer hover:ring-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary' : ''
  }`
  const Behaelter = klickbar ? 'button' : 'div'
  const klickAttribute = klickbar
    ? {
        type: 'button' as const,
        onClick: () => onSelect?.(participant),
        title: t('calls.optionsFor', { name: participant.username }),
      }
    : {}

  if (compact) {
    return (
      <Behaelter
        {...klickAttribute}
        className={`flex items-center gap-2.5 rounded-2xl bg-surface-container-high/70 px-2.5 py-2 ${gemeinsam}`}
      >
        <Avatar src={participant.avatarUrl} name={participant.username} size="sm" className="shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-on-surface">
            {participant.username}
            {participant.isSelf && <span className="text-on-surface-variant"> (du)</span>}
          </div>
          <div className="truncate text-label-sm text-on-surface-variant">
            {zustandstext(participant, t)}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 text-on-surface-variant">
          {lokalStumm && <VolumeX className="h-3.5 w-3.5 text-status-warning" aria-label={t('calls.mutedForYou')} />}
          {participant.isMuted ? (
            <MicOff className="h-3.5 w-3.5 text-status-destructive" aria-label={t('calls.micOff')} />
          ) : (
            <Mic className="h-3.5 w-3.5" aria-label={t('calls.micOn')} />
          )}
          {participant.isCameraOff ? (
            <VideoOff className="h-3.5 w-3.5" aria-label={t('calls.cameraOff')} />
          ) : (
            <Video className="h-3.5 w-3.5 text-primary" aria-label={t('calls.cameraOn')} />
          )}
        </div>
      </Behaelter>
    )
  }

  return (
    <Behaelter
      {...klickAttribute}
      className={`relative flex h-full items-center justify-center overflow-hidden rounded-2xl bg-surface-container ${gemeinsam}`}
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
        <Avatar
          src={participant.avatarUrl}
          name={participant.username}
          size="xl"
          className="shrink-0"
        />
      )}

      <div className="pointer-events-none absolute inset-x-2 bottom-2 flex items-center justify-between gap-2">
        <span className="min-w-0 truncate rounded-full bg-surface/85 px-2 py-0.5 text-label-sm font-medium text-on-surface backdrop-blur-sm">
          {participant.username}
          {participant.isSelf && <span className="text-on-surface-variant"> (du)</span>}
        </span>
        <span className="flex shrink-0 items-center gap-1 rounded-full bg-surface/85 px-2 py-0.5 text-on-surface-variant backdrop-blur-sm">
          {lokalStumm && <VolumeX className="h-3.5 w-3.5 text-status-warning" aria-label={t('calls.mutedForYou')} />}
          {participant.isMuted ? (
            <MicOff className="h-3.5 w-3.5 text-status-destructive" aria-label={t('calls.micOff')} />
          ) : (
            <Mic className="h-3.5 w-3.5" aria-label={t('calls.micOn')} />
          )}
        </span>
      </div>

      {participant.isPending && (
        <span className="pointer-events-none absolute left-2 top-2 rounded-full bg-status-warning/20 px-2 py-0.5 text-label-sm uppercase tracking-wider text-status-warning">
          Verbindet
        </span>
      )}
    </Behaelter>
  )
}
