import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { createPortal } from 'react-dom'
import {
  Headphones,
  HeadphoneOff,
  Loader2,
  Maximize2,
  Mic,
  MicOff,
  Monitor,
  MonitorOff,
  Phone,
  PhoneOff,
  Settings,
  ShieldCheck,
  UserPlus,
  Users,
  Video as VideoIcon,
  VideoOff,
  Volume2,
} from 'lucide-react'
import { Button } from '@/Singra/UI'
import { Avatar } from '@/Singra/UI/Avatar'
import { aktiverRaum, useCallStore, type CallParticipant } from '@/stores/useCallStore'
import { bildschirmfreigabeMoeglich } from '@/services/livekitRaum'
import { sendeGeraeteBenachrichtigung } from '@/lib/benachrichtigung'
import type { DropdownOption } from '@/components/ui/Dropdown'
import type { Track } from 'livekit-client'
import { AddParticipantModal } from './AddParticipantModal'
import { DeviceSelectorModal } from './DeviceSelectorModal'
import { ParticipantMenu } from './ParticipantMenu'
import { ParticipantTile } from './ParticipantTile'
import { RemoteAudio } from './RemoteAudio'
import { ScreenShareOptionsModal } from './ScreenShareOptionsModal'

function formatiereDauer(sekunden: number): string {
  const s = Math.max(0, sekunden)
  const m = Math.floor(s / 60)
  const rest = s % 60
  return `${m.toString().padStart(2, '0')}:${rest.toString().padStart(2, '0')}`
}

/**
 * Wie viele Kacheln nebeneinander passen. Feste Stufen statt `auto-fit`: so ist
 * jede Kachel breiter als hoch und keine wird zum Streifen, wenn ein Achter
 * dazukommt.
 */
function rasterKlassen(anzahl: number): string {
  if (anzahl <= 1) return 'grid-cols-1'
  if (anzahl <= 4) return 'grid-cols-1 sm:grid-cols-2'
  if (anzahl <= 9) return 'grid-cols-2 lg:grid-cols-3'
  return 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4'
}

/** Bühne für eine Bildschirmfreigabe. */
const ShareStage: React.FC<{ track: Track; ownerName: string; isSelf: boolean }> = ({
  track,
  ownerName,
  isSelf,
}) => {
  const { t } = useTranslation()

  const ref = useRef<HTMLVideoElement | null>(null)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    track.attach(element)
    return () => {
      track.detach(element)
    }
  }, [track])

  return (
    <div className="relative h-full w-full overflow-hidden rounded-2xl border border-primary/25 bg-surface-container-lowest">
      <video ref={ref} autoPlay playsInline muted={isSelf} className="h-full w-full object-contain" />
      <span className="absolute left-3 top-3 flex max-w-[calc(100%-1.5rem)] items-center gap-1.5 truncate rounded-full bg-surface/85 px-2.5 py-1 text-label-sm uppercase tracking-[0.16em] text-primary backdrop-blur-sm">
        <Monitor className="h-3 w-3 shrink-0" />
        <span className="truncate">
          {isSelf ? t('calls.yourShare') : t('calls.isSharing', { name: ownerName })}
        </span>
      </span>
    </div>
  )
}

export const CallOverlay: React.FC = () => {
  const { t } = useTranslation()

  const {
    state,
    kind,
    mode,
    partner,
    group,
    raum,
    participants,
    screenShares,
    focusedShareIdentity,
    isMuted,
    isDeafened,
    isCameraOff,
    isScreenSharing,
    screenShareOptions,
    selectedAudioInput,
    selectedVideoInput,
    selectedAudioOutput,
    callDurationSeconds,
    reconnecting,
    errorMessage,
    audioBlockiert,
    serverStumm,
    hinweise,
    acceptCall,
    rejectCall,
    endCall,
    toggleMute,
    toggleDeafen,
    toggleCamera,
    startScreenShare,
    stopScreenShare,
    setFocusedShare,
    setParticipantVolume,
    inviteToCall,
    setDevices,
    incrementDuration,
    erlaubeTon,
  } = useCallStore()

  const [geraeteDialogOffen, setGeraeteDialogOffen] = useState(false)
  const [freigabeDialogOffen, setFreigabeDialogOffen] = useState(false)
  const [einladenDialogOffen, setEinladenDialogOffen] = useState(false)
  const [gewaehlterTeilnehmer, setGewaehlterTeilnehmer] = useState<string | null>(null)
  const [audioEingaenge, setAudioEingaenge] = useState<DropdownOption[]>([])
  const [videoEingaenge, setVideoEingaenge] = useState<DropdownOption[]>([])
  const [audioAusgaenge, setAudioAusgaenge] = useState<DropdownOption[]>([])

  const klingeltonRef = useRef<{ context: AudioContext; timer: number } | null>(null)
  const [klingeltonBlockiert, setKlingeltonBlockiert] = useState(false)

  const istGruppe = kind === 'gruppe'
  const partnerVerbunden = istGruppe || participants.some((p) => !p.isSelf)
  const fokussierteFreigabe = useMemo(
    () => screenShares.find((f) => f.identity === focusedShareIdentity) ?? null,
    [screenShares, focusedShareIdentity],
  )
  const teilnehmerIds = useMemo(() => participants.map((p) => p.userId), [participants])
  const freigabeMoeglich = useMemo(() => bildschirmfreigabeMoeglich(), [])
  // Das LiveKit-Objekt liegt bewusst außerhalb des Stores. Es hier neu zu holen,
  // sobald der Zustand wechselt, reicht: die Tonwiedergabe hängt sich an, sobald
  // die Verbindung steht, und wird beim Ende wieder abgeräumt.
  const room = useMemo(() => aktiverRaum(), [state, raum])
  const menueTeilnehmer = useMemo(
    () => participants.find((p) => p.identity === gewaehlterTeilnehmer) ?? null,
    [participants, gewaehlterTeilnehmer],
  )

  useEffect(() => {
    if (state !== 'active' || !partnerVerbunden) return
    const intervall = window.setInterval(() => incrementDuration(), 1000)
    return () => window.clearInterval(intervall)
  }, [state, partnerVerbunden, incrementDuration])

  // Sobald eine zweite Freigabe dazukommt oder die fokussierte verschwindet,
  // muss die Bühne etwas Sinnvolles zeigen statt ins Leere.
  useEffect(() => {
    if (focusedShareIdentity && !screenShares.some((f) => f.identity === focusedShareIdentity)) {
      setFocusedShare(null)
    }
    if (!focusedShareIdentity && screenShares.length === 1) {
      setFocusedShare(screenShares[0].identity)
    }
  }, [screenShares, focusedShareIdentity, setFocusedShare])

  // Wer den Anruf verlässt, während sein Menü offen ist, soll kein leeres
  // Fenster hinterlassen.
  useEffect(() => {
    if (gewaehlterTeilnehmer && !menueTeilnehmer) setGewaehlterTeilnehmer(null)
  }, [gewaehlterTeilnehmer, menueTeilnehmer])

  // Globaler Empfang von Anrufereignissen über msm:sync-event (auch außerhalb des Chats)
  useEffect(() => {
    const onSyncEvent = (e: Event) => {
      const custom = e as CustomEvent<{ type?: string; [key: string]: unknown }>
      if (custom.detail) {
        useCallStore.getState().handleCallSyncEvent(custom.detail)
      }
    }
    window.addEventListener('msm:sync-event', onSyncEvent)
    return () => window.removeEventListener('msm:sync-event', onSyncEvent)
  }, [])

  const stoppeKlingelton = () => {
    const aktuell = klingeltonRef.current
    if (!aktuell) return
    window.clearInterval(aktuell.timer)
    aktuell.context.close().catch(() => {})
    klingeltonRef.current = null
  }

  const starteKlingelton = () => {
    if (typeof window === 'undefined' || klingeltonRef.current) return
    const AudioContextCtor =
      window.AudioContext ||
      (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AudioContextCtor) return
    const context = new AudioContextCtor()
    const spiele = () => {
      if (context.state === 'suspended') {
        setKlingeltonBlockiert(true)
        return
      }
      const oszillator = context.createOscillator()
      const gain = context.createGain()
      oszillator.frequency.value = 660
      gain.gain.setValueAtTime(0.0001, context.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.12, context.currentTime + 0.03)
      gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.45)
      oszillator.connect(gain).connect(context.destination)
      oszillator.start()
      oszillator.stop(context.currentTime + 0.5)
    }
    klingeltonRef.current = { context, timer: window.setInterval(spiele, 1100) }
    spiele()
  }

  useEffect(() => {
    if (state === 'incoming') {
      starteKlingelton()
      void sendeGeraeteBenachrichtigung({
        titel: t('calls.incoming'),
        text: t('calls.callsYou', { name: partner?.username ?? t('calls.someone') }),
      })
    } else {
      stoppeKlingelton()
    }
    return () => stoppeKlingelton()
  }, [state, partner?.username])

  useEffect(() => () => stoppeKlingelton(), [])

  useEffect(() => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) return
    navigator.mediaDevices
      .enumerateDevices()
      .then((geraete) => {
        const audioIn: DropdownOption[] = []
        const videoIn: DropdownOption[] = []
        const audioOut: DropdownOption[] = []
        geraete.forEach((geraet, index) => {
          if (geraet.kind === 'audioinput') {
            audioIn.push({ value: geraet.deviceId || `mic-${index}`, label: geraet.label || t('profile.audioMicrophoneFallback', { number: index + 1 }) })
          } else if (geraet.kind === 'videoinput') {
            videoIn.push({ value: geraet.deviceId || `cam-${index}`, label: geraet.label || t('calls.cameraFallback', { number: index + 1 }) })
          } else if (geraet.kind === 'audiooutput') {
            audioOut.push({ value: geraet.deviceId || `speaker-${index}`, label: geraet.label || t('profile.audioSpeakerFallback', { number: index + 1 }) })
          }
        })
        if (audioIn.length) setAudioEingaenge(audioIn)
        if (videoIn.length) setVideoEingaenge(videoIn)
        if (audioOut.length) setAudioAusgaenge(audioOut)
      })
      .catch(() => {})
  }, [state])

  if (state === 'idle') return null

  const kopfStatus = reconnecting
    ? t('calls.reconnecting')
    : state === 'outgoing' || (!partnerVerbunden && state !== 'incoming' && state !== 'connecting')
      ? t('calls.ringing')
      : state === 'incoming'
        ? t('calls.incoming')
        : state === 'connecting'
          ? t('calls.connecting')
          : t('calls.connectedFor', { duration: formatiereDauer(callDurationSeconds) })

  const titel = istGruppe ? group?.name || t('calls.groupCall') : partner?.username || t('calls.peer')
  const kopfBild = istGruppe ? group?.avatarUrl : partner?.avatarUrl

  /** Runder Knopf der Steuerleiste. Ein Ort für Größe, Form und Zustandsfarbe. */
  const steuerKnopf = (aktiv: boolean, ton: 'neutral' | 'warnung' | 'aktion' = 'neutral') => {
    if (!aktiv) {
      return 'h-11 w-11 rounded-full bg-surface-container-high text-on-surface hover:bg-surface-container-highest'
    }
    if (ton === 'warnung') return 'h-11 w-11 rounded-full bg-status-destructive/20 text-status-destructive hover:bg-status-destructive/30'
    if (ton === 'aktion') return 'h-11 w-11 rounded-full bg-primary/20 text-primary hover:bg-primary/30'
    return 'h-11 w-11 rounded-full bg-secondary/20 text-secondary hover:bg-secondary/30'
  }

  // Über einen Portal an `document.body`, nicht dort, wo die Messenger-Seite
  // steht. Der Inhaltsbereich der App-Hülle liegt in zwei Stapelkontexten
  // (`relative z-10` in `Shell.tsx`); ein `z-50` darin zählt nach außen nur als
  // 10 und verliert gegen die Navigation (`z-40`). Die hat deshalb über den
  // linken 256 Pixeln des Anruffensters gelegen, und der Rest sah aus, als
  // liefe er nach rechts hinaus. Ein höherer z-Wert hilft nicht — er bliebe im
  // selben Käfig. Der Portal nimmt das Fenster aus dem Käfig heraus.
  return createPortal(
    <div className="fixed inset-0 z-50 flex select-none flex-col justify-between overflow-hidden bg-surface/95 text-on-surface backdrop-blur-md animate-fade-in">
      <RemoteAudio room={room} />

      {/* Kopf */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-outline-variant/40 bg-surface-container-low/60 p-3 sm:p-4">
        <div className="flex min-w-0 items-center gap-2.5">
          {kopfBild ? (
            <Avatar src={kopfBild} name={titel} size="sm" className="shrink-0" />
          ) : (
            <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-status-success/15 text-status-success">
              {istGruppe ? <Users className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
            </div>
          )}
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-1.5 text-sm font-semibold">
              <span className="truncate">{titel}</span>
              <span className="shrink-0 rounded-full bg-status-success/20 px-1.5 py-0.5 font-mono text-label-sm uppercase tracking-wider text-status-success">
                {participants.length > 0 ? t('calls.liveCount', { count: participants.length }) : t('calls.e2ee')}
              </span>
            </div>
            <div className="flex min-w-0 items-center gap-1.5 text-xs text-on-surface-variant">
              {(state === 'connecting' || reconnecting) && (
                <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
              )}
              <span className="truncate">{kopfStatus}</span>
            </div>
          </div>
        </div>

        <Button
          variant="ghost"
          size="icon"
          onClick={() => setGeraeteDialogOffen(true)}
          className="shrink-0 rounded-full text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
          title={t('calls.chooseDevices')}
          aria-label={t('calls.chooseDevices')}
        >
          <Settings className="h-5 w-5" />
        </Button>
      </div>

      {errorMessage && (
        <div className="msm-alert-warning mx-3 mt-3 shrink-0 rounded-2xl px-3 py-2 text-xs leading-relaxed sm:mx-4">
          {errorMessage}
        </div>
      )}

      {audioBlockiert && state !== 'incoming' && (
        <div className="mx-3 mt-3 flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-2xl border border-status-warning/40 bg-status-warning/10 px-3 py-2 sm:mx-4">
          <span className="text-xs leading-relaxed text-on-surface">
            Dein Browser lässt den Ton erst nach einem Klick zu.
          </span>
          <Button size="sm" onClick={() => void erlaubeTon()} className="gap-1.5">
            <Volume2 className="h-4 w-4" /> Ton aktivieren
          </Button>
        </div>
      )}

      {/* Bühne */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-hidden p-3 sm:p-4 lg:flex-row lg:gap-4">
        {state === 'incoming' && partner ? (
          <div className="m-auto flex w-full max-w-md flex-col items-center gap-6 rounded-3xl border border-status-success/30 bg-surface-container-low p-8 text-center shadow-2xl">
            <div className="relative">
              <Avatar
                src={partner.avatarUrl}
                name={partner.username}
                size="2xl"
                className="border-4 border-status-success/50"
              />
              <span className="absolute inset-0 animate-ping rounded-full border-2 border-status-success/50" />
            </div>
            <div>
              <h2 className="font-headline text-2xl font-bold text-on-surface">{partner.username}</h2>
              <p className="mt-1 text-sm text-on-surface-variant">
                {mode === 'video' ? t('calls.incomingVideoCall') : t('calls.incomingAudioCall')}
              </p>
            </div>
            {klingeltonBlockiert && (
              <button
                type="button"
                onClick={() => {
                  klingeltonRef.current?.context
                    .resume()
                    .then(() => setKlingeltonBlockiert(false))
                    .catch(() => {})
                }}
                className="text-xs text-status-warning underline"
              >
                {t('calls.enableRingtone')}
              </button>
            )}
            <div className="flex w-full justify-center gap-4">
              <Button variant="destructive" onClick={rejectCall} className="flex-1 rounded-full">
                <PhoneOff className="mr-2 h-5 w-5" /> {t('calls.decline')}
              </Button>
              <Button
                onClick={() => {
                  stoppeKlingelton()
                  void acceptCall()
                }}
                className="flex-1 rounded-full bg-status-success text-surface hover:bg-status-success/90"
              >
                <Phone className="mr-2 h-5 w-5" /> {t('calls.accept')}
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex min-h-[14rem] min-w-0 flex-1 flex-col gap-3 overflow-hidden lg:min-h-0">
              {screenShares.length > 1 && (
                <div className="flex min-w-0 shrink-0 items-center gap-2 overflow-x-auto pb-1">
                  <button
                    type="button"
                    onClick={() => setFocusedShare(null)}
                    className={`shrink-0 rounded-xl border px-3 py-1.5 text-xs transition ${
                      focusedShareIdentity === null
                        ? 'border-primary/60 bg-primary/15 text-primary'
                        : 'border-outline-variant/40 bg-surface-container-low text-on-surface-variant hover:bg-surface-container-high'
                    }`}
                    aria-pressed={focusedShareIdentity === null}
                  >
                    Alle nebeneinander
                  </button>
                  {screenShares.map((freigabe) => (
                    <button
                      key={freigabe.identity}
                      type="button"
                      onClick={() => setFocusedShare(freigabe.identity)}
                      className={`flex max-w-[12rem] shrink-0 items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs transition ${
                        freigabe.identity === focusedShareIdentity
                          ? 'border-primary/60 bg-primary/15 text-primary'
                          : 'border-outline-variant/40 bg-surface-container-low text-on-surface-variant hover:bg-surface-container-high'
                      }`}
                    >
                      <Maximize2 className="h-3 w-3 shrink-0" />
                      <span className="truncate">{freigabe.ownerName}</span>
                    </button>
                  ))}
                </div>
              )}

              <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
                {fokussierteFreigabe ? (
                  <ShareStage
                    track={fokussierteFreigabe.track}
                    ownerName={fokussierteFreigabe.ownerName}
                    isSelf={participants.some(
                      (t) => t.isSelf && t.identity === fokussierteFreigabe.identity,
                    )}
                  />
                ) : screenShares.length > 0 ? (
                  <div className="grid h-full auto-rows-fr grid-cols-1 gap-2 sm:grid-cols-2">
                    {screenShares.map((freigabe) => (
                      <button
                        key={freigabe.identity}
                        type="button"
                        onClick={() => setFocusedShare(freigabe.identity)}
                        className="min-w-0 overflow-hidden rounded-2xl text-left"
                      >
                        <ShareStage
                          track={freigabe.track}
                          ownerName={freigabe.ownerName}
                          isSelf={participants.some(
                            (t) => t.isSelf && t.identity === freigabe.identity,
                          )}
                        />
                      </button>
                    ))}
                  </div>
                ) : participants.length > 0 && partnerVerbunden ? (
                  <div
                    className={`grid h-full auto-rows-fr gap-2 overflow-y-auto ${rasterKlassen(
                      participants.length,
                    )}`}
                  >
                    {participants.map((teilnehmer) => (
                      <ParticipantTile
                        key={teilnehmer.identity}
                        participant={teilnehmer}
                        onSelect={(t: CallParticipant) => setGewaehlterTeilnehmer(t.identity)}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-4 text-on-surface-variant">
                    <Avatar
                      src={partner?.avatarUrl}
                      name={titel}
                      size="2xl"
                      className="border-2 border-status-success/30 bg-status-success/10"
                    />
                    <div className="max-w-full px-4 text-center">
                      <div className="truncate text-base font-semibold text-on-surface">{titel}</div>
                      <div className="text-xs text-on-surface-variant">
                        {!partnerVerbunden ? t('calls.waitingForAnswer') : t('calls.connecting')}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Teilnehmerleiste. Bei Bildschirmfreigabe ist sie der einzige Ort,
                an dem man die Gesichter noch sieht — deshalb immer sichtbar.
                Sie scrollt in ihrer eigenen Achse: quer auf schmalen Fenstern,
                längs ab `lg`. Nichts darin darf breiter werden als sie selbst. */}
            {participants.length > 0 && partnerVerbunden && (
              <aside className="flex w-full min-w-0 shrink-0 flex-col overflow-hidden rounded-3xl border border-outline-variant/40 bg-surface-container-low/70 p-3 lg:w-[17rem]">
                <div className="mb-2 flex shrink-0 items-center justify-between text-label-sm uppercase tracking-[0.18em] text-on-surface-variant">
                  <span>{t('calls.inCall')}</span>
                  <span>{participants.length}</span>
                </div>
                <div className="flex min-h-0 min-w-0 gap-2 overflow-x-auto overflow-y-hidden pb-1 lg:flex-col lg:overflow-x-hidden lg:overflow-y-auto lg:pb-0">
                  {participants.map((teilnehmer) => (
                    <div
                      key={teilnehmer.identity}
                      className="w-[13rem] shrink-0 lg:w-full lg:shrink"
                    >
                      <ParticipantTile
                        participant={teilnehmer}
                        compact
                        onSelect={(t: CallParticipant) => setGewaehlterTeilnehmer(t.identity)}
                      />
                    </div>
                  ))}
                </div>
              </aside>
            )}
          </>
        )}
      </div>

      {/* Steuerleiste */}
      {state !== 'incoming' && (
        <div className="relative shrink-0 border-t border-outline-variant/40 bg-surface-container-low/90 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur-md sm:p-4">
          {/* Flüchtige Meldungen, direkt über der Steuerleiste statt in der
              Bühne: dort verdeckten sie auf schmalen Fenstern die Gesichter.
              Nichts davon landet im Chatverlauf. */}
          {hinweise.length > 0 && (
            <div
              className="pointer-events-none absolute bottom-full left-1/2 mb-2 flex w-full max-w-sm -translate-x-1/2 flex-col items-center gap-1.5 px-4"
              aria-live="polite"
            >
              {hinweise.map((hinweis) => (
                <span
                  key={hinweis.id}
                  className={`max-w-full truncate rounded-full border px-3 py-1.5 text-xs shadow-lg backdrop-blur-sm animate-slide-up ${
                    hinweis.art === 'beitritt'
                      ? 'border-status-success/40 bg-status-success/15 text-status-success'
                      : hinweis.art === 'info'
                        ? 'border-outline-variant/60 bg-surface-container-high/90 text-on-surface'
                        : 'border-outline-variant/50 bg-surface-container-high/90 text-on-surface-variant'
                  }`}
                >
                  {hinweis.art === 'beitritt' ? '↳ ' : hinweis.art === 'abgang' ? '↰ ' : ''}
                  {hinweis.text}
                </span>
              ))}
            </div>
          )}
          <div className="mx-auto flex max-w-2xl flex-wrap items-center justify-center gap-2 sm:gap-3">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleMute}
              className={steuerKnopf(isMuted, 'warnung')}
              title={
                serverStumm
                  ? t('calls.mutedByModerator')
                  : isMuted
                    ? t('calls.micEnable')
                    : t('calls.micMute')
              }
              aria-label={isMuted ? t('calls.micEnable') : t('calls.micMute')}
              aria-pressed={isMuted}
            >
              {isMuted ? <MicOff className="h-5 w-5" /> : <Mic className="h-5 w-5" />}
            </Button>

            <Button
              variant="ghost"
              size="icon"
              onClick={toggleDeafen}
              className={steuerKnopf(isDeafened, 'warnung')}
              title={isDeafened ? t('calls.playbackEnable') : t('calls.playbackMuteHint')}
              aria-label={isDeafened ? t('calls.playbackEnable') : t('calls.playbackMute')}
              aria-pressed={isDeafened}
            >
              {isDeafened ? <HeadphoneOff className="h-5 w-5" /> : <Headphones className="h-5 w-5" />}
            </Button>

            <Button
              variant="ghost"
              size="icon"
              onClick={toggleCamera}
              className={steuerKnopf(!isCameraOff, 'aktion')}
              title={isCameraOff ? t('calls.cameraEnable') : t('calls.cameraDisable')}
              aria-label={isCameraOff ? t('calls.cameraEnable') : t('calls.cameraDisable')}
              aria-pressed={!isCameraOff}
            >
              {isCameraOff ? <VideoOff className="h-5 w-5" /> : <VideoIcon className="h-5 w-5" />}
            </Button>

            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                if (isScreenSharing) void stopScreenShare()
                else setFreigabeDialogOffen(true)
              }}
              disabled={!freigabeMoeglich}
              className={`${steuerKnopf(isScreenSharing, 'aktion')} ${freigabeMoeglich ? '' : 'opacity-50'}`}
              title={
                freigabeMoeglich
                  ? isScreenSharing
                    ? t('calls.stopShare')
                    : t('calls.shareScreen')
                  : t('calls.shareNotPossible')
              }
              aria-label={isScreenSharing ? t('calls.stopShare') : t('calls.shareScreen')}
              aria-pressed={isScreenSharing}
            >
              {isScreenSharing ? <MonitorOff className="h-5 w-5" /> : <Monitor className="h-5 w-5" />}
            </Button>

            {kind === 'direkt' && state === 'active' && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setEinladenDialogOffen(true)}
                className={steuerKnopf(false)}
                title={t('calls.addParticipant')}
                aria-label={t('calls.addParticipant')}
              >
                <UserPlus className="h-5 w-5" />
              </Button>
            )}

            {/* Auflegen steht abgesetzt und beschriftet: der eine Knopf, den man
                im Zweifel sofort finden muss. */}
            <span className="mx-1 hidden h-8 w-px bg-outline-variant/50 sm:block" aria-hidden="true" />
            <Button
              variant="destructive"
              onClick={endCall}
              className="h-11 gap-2 rounded-full px-4 sm:px-5"
              title={t('calls.endCall')}
              aria-label={t('calls.endCall')}
            >
              <PhoneOff className="h-5 w-5" />
              <span className="hidden sm:inline">{t('calls.hangUp')}</span>
            </Button>
          </div>
        </div>
      )}

      <ParticipantMenu
        participant={menueTeilnehmer}
        onClose={() => setGewaehlterTeilnehmer(null)}
        onVolumeChange={setParticipantVolume}
        raum={istGruppe ? raum : null}
        darfStummschalten={istGruppe && group?.canMute === true}
        darfEntfernen={istGruppe && group?.canKick === true}
      />

      <DeviceSelectorModal
        isOpen={geraeteDialogOffen}
        onClose={() => setGeraeteDialogOffen(false)}
        audioInputs={audioEingaenge}
        videoInputs={videoEingaenge}
        audioOutputs={audioAusgaenge}
        selectedAudioInput={selectedAudioInput}
        selectedVideoInput={selectedVideoInput}
        selectedAudioOutput={selectedAudioOutput}
        onSelectAudioInput={(id) => setDevices(id, undefined, undefined)}
        onSelectVideoInput={(id) => setDevices(undefined, id, undefined)}
        onSelectAudioOutput={(id) => setDevices(undefined, undefined, id)}
      />

      <ScreenShareOptionsModal
        isOpen={freigabeDialogOffen}
        onClose={() => setFreigabeDialogOffen(false)}
        onStart={(optionen) => void startScreenShare(optionen)}
        initial={screenShareOptions}
      />

      <AddParticipantModal
        isOpen={einladenDialogOffen}
        onClose={() => setEinladenDialogOffen(false)}
        bereitsImAnruf={teilnehmerIds}
        onInvite={inviteToCall}
      />
    </div>,
    document.body,
  )
}
