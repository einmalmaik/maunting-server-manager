import React, { useEffect, useMemo, useRef, useState } from 'react'
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
  User,
  UserPlus,
  Users,
  Video as VideoIcon,
  VideoOff,
} from 'lucide-react'
import { Button } from '@/Singra/UI'
import { apiUrl } from '@/config/api'
import { useCallStore } from '@/stores/useCallStore'
import { bildschirmfreigabeMoeglich } from '@/services/livekitRaum'
import { sendeGeraeteBenachrichtigung } from '@/lib/benachrichtigung'
import type { DropdownOption } from '@/components/ui/Dropdown'
import type { Track } from 'livekit-client'
import { AddParticipantModal } from './AddParticipantModal'
import { DeviceSelectorModal } from './DeviceSelectorModal'
import { ParticipantTile } from './ParticipantTile'
import { ScreenShareOptionsModal } from './ScreenShareOptionsModal'

function formatiereDauer(sekunden: number): string {
  const s = Math.max(0, sekunden)
  const m = Math.floor(s / 60)
  const rest = s % 60
  return `${m.toString().padStart(2, '0')}:${rest.toString().padStart(2, '0')}`
}

/** Bühne für eine Bildschirmfreigabe. */
const ShareStage: React.FC<{ track: Track; ownerName: string; isSelf: boolean }> = ({
  track,
  ownerName,
  isSelf,
}) => {
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
    <div className="relative h-full w-full overflow-hidden rounded-2xl border border-cyan-400/20 bg-slate-950">
      <video ref={ref} autoPlay playsInline muted={isSelf} className="h-full w-full object-contain" />
      <span className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-slate-950/75 px-2.5 py-1 text-[10px] uppercase tracking-[0.16em] text-cyan-200">
        <Monitor className="h-3 w-3" />
        {isSelf ? 'Deine Freigabe' : `${ownerName} teilt`}
      </span>
    </div>
  )
}

export const CallOverlay: React.FC = () => {
  const {
    state,
    kind,
    mode,
    partner,
    group,
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
    acceptCall,
    rejectCall,
    endCall,
    toggleMute,
    toggleDeafen,
    toggleCamera,
    startScreenShare,
    stopScreenShare,
    setFocusedShare,
    inviteToCall,
    setDevices,
    incrementDuration,
  } = useCallStore()

  const [geraeteDialogOffen, setGeraeteDialogOffen] = useState(false)
  const [freigabeDialogOffen, setFreigabeDialogOffen] = useState(false)
  const [einladenDialogOffen, setEinladenDialogOffen] = useState(false)
  const [audioEingaenge, setAudioEingaenge] = useState<DropdownOption[]>([])
  const [videoEingaenge, setVideoEingaenge] = useState<DropdownOption[]>([])
  const [audioAusgaenge, setAudioAusgaenge] = useState<DropdownOption[]>([])
  const [avatarKaputt, setAvatarKaputt] = useState(false)

  const klingeltonRef = useRef<{ context: AudioContext; timer: number } | null>(null)
  const [klingeltonBlockiert, setKlingeltonBlockiert] = useState(false)

  const istGruppe = kind === 'gruppe'
  const fokussierteFreigabe = useMemo(
    () => screenShares.find((f) => f.identity === focusedShareIdentity) ?? null,
    [screenShares, focusedShareIdentity],
  )
  const teilnehmerIds = useMemo(() => participants.map((t) => t.userId), [participants])
  const freigabeMoeglich = useMemo(() => bildschirmfreigabeMoeglich(), [])

  useEffect(() => {
    setAvatarKaputt(false)
  }, [partner?.avatarUrl, partner?.userId])

  useEffect(() => {
    if (state !== 'active') return
    const intervall = window.setInterval(() => incrementDuration(), 1000)
    return () => window.clearInterval(intervall)
  }, [state, incrementDuration])

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
        titel: 'Eingehender Anruf',
        text: `${partner?.username ?? 'Jemand'} möchte dich anrufen.`,
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
            audioIn.push({ value: geraet.deviceId || `mic-${index}`, label: geraet.label || `Mikrofon ${index + 1}` })
          } else if (geraet.kind === 'videoinput') {
            videoIn.push({ value: geraet.deviceId || `cam-${index}`, label: geraet.label || `Kamera ${index + 1}` })
          } else if (geraet.kind === 'audiooutput') {
            audioOut.push({ value: geraet.deviceId || `speaker-${index}`, label: geraet.label || `Lautsprecher ${index + 1}` })
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
    ? 'Verbindung wird wiederhergestellt…'
    : state === 'outgoing'
      ? 'Klingelt…'
      : state === 'incoming'
        ? 'Eingehender Anruf'
        : state === 'connecting'
          ? 'Verbindet…'
          : `Verbunden · ${formatiereDauer(callDurationSeconds)}`

  const titel = istGruppe ? group?.name || 'Gruppenanruf' : partner?.username || 'Gesprächspartner'
  const kopfBild = istGruppe ? group?.avatarUrl : partner?.avatarUrl

  return (
    <div className="fixed inset-0 z-50 flex select-none flex-col justify-between bg-slate-950/95 text-white backdrop-blur-md animate-in fade-in duration-200">
      {/* Kopf */}
      <div className="flex items-center justify-between border-b border-white/10 bg-slate-900/40 p-3 sm:p-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-emerald-500/15 text-emerald-300">
            {kopfBild ? (
              <img src={apiUrl(kopfBild)} alt="" className="h-full w-full object-cover" />
            ) : istGruppe ? (
              <Users className="h-5 w-5" />
            ) : (
              <ShieldCheck className="h-5 w-5" />
            )}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-sm font-semibold">
              <span className="truncate">{titel}</span>
              <span className="shrink-0 rounded-full bg-emerald-500/20 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-emerald-300">
                {participants.length > 0 ? `${participants.length} live` : 'E2EE'}
              </span>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-white/60">
              {(state === 'connecting' || reconnecting) && (
                <Loader2 className="h-3 w-3 animate-spin" />
              )}
              <span className="truncate">{kopfStatus}</span>
            </div>
          </div>
        </div>

        <Button
          variant="ghost"
          size="icon"
          onClick={() => setGeraeteDialogOffen(true)}
          className="rounded-full text-white/80 hover:bg-white/10 hover:text-white"
          title="Geräte auswählen"
          aria-label="Geräte auswählen"
        >
          <Settings className="h-5 w-5" />
        </Button>
      </div>

      {errorMessage && (
        <div className="mx-3 mt-3 rounded-2xl border border-amber-400/30 bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-50 sm:mx-4">
          {errorMessage}
        </div>
      )}

      {/* Bühne */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 sm:p-4 lg:flex-row lg:gap-4 lg:overflow-hidden">
        {state === 'incoming' && partner ? (
          <div className="m-auto flex w-full max-w-md flex-col items-center gap-6 rounded-3xl border border-emerald-400/30 bg-slate-900/80 p-8 text-center shadow-2xl">
            <div className="relative">
              <div className="h-28 w-28 overflow-hidden rounded-full border-4 border-emerald-400/50 bg-emerald-500/15">
                {partner.avatarUrl && !avatarKaputt ? (
                  <img
                    src={apiUrl(partner.avatarUrl)}
                    alt={partner.username}
                    className="h-full w-full object-cover"
                    onError={() => setAvatarKaputt(true)}
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center text-3xl font-bold text-emerald-100">
                    {partner.username.slice(0, 2).toUpperCase()}
                  </div>
                )}
              </div>
              <span className="absolute inset-0 animate-ping rounded-full border-2 border-emerald-400/50" />
            </div>
            <div>
              <h2 className="text-2xl font-bold">{partner.username}</h2>
              <p className="mt-1 text-sm text-white/65">
                Eingehender {mode === 'video' ? 'Video-' : ''}Anruf
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
                className="text-xs text-amber-200 underline"
              >
                Klingelton aktivieren
              </button>
            )}
            <div className="flex w-full justify-center gap-4">
              <Button variant="destructive" onClick={rejectCall} className="flex-1 rounded-full">
                <PhoneOff className="mr-2 h-5 w-5" /> Ablehnen
              </Button>
              <Button
                onClick={() => {
                  stoppeKlingelton()
                  void acceptCall()
                }}
                className="flex-1 rounded-full bg-emerald-600 hover:bg-emerald-500"
              >
                <Phone className="mr-2 h-5 w-5" /> Annehmen
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex min-h-[min(48vh,32rem)] min-w-0 flex-1 flex-col gap-3 lg:min-h-0">
              {screenShares.length > 1 && (
                <div className="flex shrink-0 items-center gap-2 overflow-x-auto pb-1">
                  <button
                    type="button"
                    onClick={() => setFocusedShare(null)}
                    className={`shrink-0 rounded-xl border px-3 py-1.5 text-xs transition ${
                      focusedShareIdentity === null
                        ? 'border-cyan-400/60 bg-cyan-500/15 text-cyan-100'
                        : 'border-white/10 bg-slate-900/60 text-white/70 hover:bg-slate-800'
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
                      className={`flex shrink-0 items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs transition ${
                        freigabe.identity === focusedShareIdentity
                          ? 'border-cyan-400/60 bg-cyan-500/15 text-cyan-100'
                          : 'border-white/10 bg-slate-900/60 text-white/70 hover:bg-slate-800'
                      }`}
                    >
                      <Maximize2 className="h-3 w-3" />
                      {freigabe.ownerName}
                    </button>
                  ))}
                </div>
              )}

              <div className="min-h-0 flex-1">
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
                        className="min-h-32 overflow-hidden rounded-2xl text-left"
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
                ) : participants.length > 0 ? (
                  <div
                    className={`grid h-full auto-rows-fr gap-2 ${
                      participants.length <= 2
                        ? 'grid-cols-1 sm:grid-cols-2'
                        : 'grid-cols-2 lg:grid-cols-3'
                    }`}
                  >
                    {participants.map((teilnehmer) => (
                      <ParticipantTile key={teilnehmer.identity} participant={teilnehmer} />
                    ))}
                  </div>
                ) : (
                  <div className="flex h-full flex-col items-center justify-center gap-4 text-white/60">
                    <div className="flex h-24 w-24 items-center justify-center rounded-full border-2 border-emerald-400/30 bg-emerald-500/10 sm:h-28 sm:w-28">
                      {partner?.avatarUrl && !avatarKaputt ? (
                        <img
                          src={apiUrl(partner.avatarUrl)}
                          alt={partner.username}
                          className="h-full w-full rounded-full object-cover"
                          onError={() => setAvatarKaputt(true)}
                        />
                      ) : (
                        <User className="h-12 w-12 sm:h-14 sm:w-14" />
                      )}
                    </div>
                    <div className="text-center">
                      <div className="text-base font-semibold">{titel}</div>
                      <div className="text-xs text-white/50">
                        {state === 'outgoing' ? 'Wartet auf Annahme' : 'Verbindet…'}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Teilnehmerleiste. Bei Bildschirmfreigabe ist sie der einzige Ort,
                an dem man die Gesichter noch sieht — deshalb immer sichtbar. */}
            {participants.length > 0 && (
              <aside className="w-full shrink-0 rounded-3xl border border-white/10 bg-slate-900/70 p-3 lg:w-[280px] lg:min-w-[280px]">
                <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-[0.18em] text-white/55">
                  <span>Im Gespräch</span>
                  <span>{participants.length}</span>
                </div>
                <div className="flex gap-2 overflow-x-auto pb-1 lg:block lg:space-y-2 lg:overflow-visible">
                  {participants.map((teilnehmer) => (
                    <div key={teilnehmer.identity} className="min-w-[210px] lg:min-w-0">
                      <ParticipantTile participant={teilnehmer} compact />
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
        <div className="sticky bottom-0 border-t border-white/10 bg-slate-900/80 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur-md sm:p-4">
          <div className="mx-auto flex max-w-xl flex-wrap items-center justify-center gap-2 sm:gap-3">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleMute}
              className={`h-11 w-11 rounded-full ${
                isMuted ? 'bg-rose-500/20 text-rose-300 hover:bg-rose-500/30' : 'bg-white/10 text-white hover:bg-white/20'
              }`}
              title={isMuted ? 'Mikrofon einschalten' : 'Mikrofon stummschalten'}
              aria-label={isMuted ? 'Mikrofon einschalten' : 'Mikrofon stummschalten'}
              aria-pressed={isMuted}
            >
              {isMuted ? <MicOff className="h-5 w-5" /> : <Mic className="h-5 w-5" />}
            </Button>

            <Button
              variant="ghost"
              size="icon"
              onClick={toggleDeafen}
              className={`h-11 w-11 rounded-full ${
                isDeafened ? 'bg-rose-500/20 text-rose-300 hover:bg-rose-500/30' : 'bg-white/10 text-white hover:bg-white/20'
              }`}
              title={isDeafened ? 'Wiedergabe einschalten' : 'Wiedergabe stummschalten (auch das eigene Mikrofon)'}
              aria-label={isDeafened ? 'Wiedergabe einschalten' : 'Wiedergabe stummschalten'}
              aria-pressed={isDeafened}
            >
              {isDeafened ? <HeadphoneOff className="h-5 w-5" /> : <Headphones className="h-5 w-5" />}
            </Button>

            <Button
              variant="ghost"
              size="icon"
              onClick={toggleCamera}
              className={`h-11 w-11 rounded-full ${
                isCameraOff ? 'bg-white/10 text-white/60 hover:bg-white/20' : 'bg-primary text-white hover:bg-primary/90'
              }`}
              title={isCameraOff ? 'Kamera einschalten' : 'Kamera ausschalten'}
              aria-label={isCameraOff ? 'Kamera einschalten' : 'Kamera ausschalten'}
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
              className={`h-11 w-11 rounded-full ${
                isScreenSharing ? 'bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30' : 'bg-white/10 text-white hover:bg-white/20'
              } ${freigabeMoeglich ? '' : 'opacity-50'}`}
              title={
                freigabeMoeglich
                  ? isScreenSharing
                    ? 'Freigabe beenden'
                    : 'Bildschirm teilen'
                  : 'Diese App kann keinen Bildschirm teilen. Im Browser funktioniert es.'
              }
              aria-label={isScreenSharing ? 'Freigabe beenden' : 'Bildschirm teilen'}
              aria-pressed={isScreenSharing}
            >
              {isScreenSharing ? <MonitorOff className="h-5 w-5" /> : <Monitor className="h-5 w-5" />}
            </Button>

            {kind === 'direkt' && state === 'active' && (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setEinladenDialogOffen(true)}
                className="h-11 w-11 rounded-full bg-white/10 text-white hover:bg-white/20"
                title="Teilnehmer hinzufügen"
                aria-label="Teilnehmer hinzufügen"
              >
                <UserPlus className="h-5 w-5" />
              </Button>
            )}

            <Button
              variant="destructive"
              size="icon"
              onClick={endCall}
              className="h-11 w-11 rounded-full bg-rose-600 text-white shadow-lg shadow-rose-600/30 hover:bg-rose-700"
              title="Anruf beenden"
              aria-label="Anruf beenden"
            >
              <PhoneOff className="h-5 w-5" />
            </Button>
          </div>
        </div>
      )}

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
    </div>
  )
}
