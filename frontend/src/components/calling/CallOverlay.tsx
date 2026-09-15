import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Mic,
  MicOff,
  Video as VideoIcon,
  VideoOff,
  PhoneOff,
  Monitor,
  Settings,
  ShieldCheck,
  User,
  Users,
  Volume2,
  LayoutGrid,
} from 'lucide-react'
import { Button } from '@/Singra/UI'
import { useCallStore } from '@/stores/useCallStore'
import { DeviceSelectorModal } from './DeviceSelectorModal'
import type { DropdownOption } from '@/components/ui/Dropdown'

export const CallOverlay: React.FC = () => {
  const {
    state,
    mode,
    partner,
    groupCall,
    isMuted,
    isCameraOff,
    isScreenSharing,
    selectedAudioInput,
    selectedVideoInput,
    selectedAudioOutput,
    callDurationSeconds,
    localStream,
    remoteStream,
    endCall,
    toggleMute,
    toggleCamera,
    setMode,
    setScreenSharing,
    setDevices,
    incrementDuration,
    setSelectedShareSource,
    toggleAudioMixer,
    updateGroupAudioMix,
  } = useCallStore()

  const [isDeviceModalOpen, setIsDeviceModalOpen] = useState(false)
  const [audioInputs, setAudioInputs] = useState<DropdownOption[]>([])
  const [videoInputs, setVideoInputs] = useState<DropdownOption[]>([])
  const [audioOutputs, setAudioOutputs] = useState<DropdownOption[]>([])

  const localVideoRef = useRef<HTMLVideoElement | null>(null)
  const remoteVideoRef = useRef<HTMLVideoElement | null>(null)

  const isGroupCall = Boolean(groupCall)
  const selectedShareSource = useMemo(
    () =>
      groupCall?.screenSources.find((source) => source.id === groupCall.selectedSourceId) ??
      null,
    [groupCall]
  )

  useEffect(() => {
    if (state !== 'active') return
    const interval = window.setInterval(() => {
      incrementDuration()
    }, 1000)
    return () => window.clearInterval(interval)
  }, [state, incrementDuration])

  useEffect(() => {
    if (localVideoRef.current && localStream) {
      localVideoRef.current.srcObject = localStream
    }
  }, [localStream, mode, isCameraOff])

  useEffect(() => {
    if (remoteVideoRef.current && remoteStream) {
      remoteVideoRef.current.srcObject = remoteStream
    }
  }, [remoteStream])

  useEffect(() => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) return
    navigator.mediaDevices
      .enumerateDevices()
      .then((devices) => {
        const aIn: DropdownOption[] = []
        const vIn: DropdownOption[] = []
        const aOut: DropdownOption[] = []

        devices.forEach((device, idx) => {
          if (device.kind === 'audioinput') {
            aIn.push({ value: device.deviceId || `mic-${idx}`, label: device.label || `Mikrofon ${idx + 1}` })
          } else if (device.kind === 'videoinput') {
            vIn.push({ value: device.deviceId || `cam-${idx}`, label: device.label || `Kamera ${idx + 1}` })
          } else if (device.kind === 'audiooutput') {
            aOut.push({ value: device.deviceId || `speaker-${idx}`, label: device.label || `Lautsprecher ${idx + 1}` })
          }
        })

        if (aIn.length) setAudioInputs(aIn)
        if (vIn.length) setVideoInputs(vIn)
        if (aOut.length) setAudioOutputs(aOut)
      })
      .catch(() => {})
  }, [])

  if (state === 'idle') return null

  const formatSeconds = (sec: number) => {
    const m = Math.floor(sec / 60)
    const s = sec % 60
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  const groupParticipantCount = groupCall?.participants.length ?? 0
  const canShareScreen = Boolean(isGroupCall ? groupCall?.permissions.canShare && groupCall.capabilities.supportsScreenShare : true)
  const canUseAudioMixer = Boolean(isGroupCall ? groupCall?.permissions.canModerate : true)
  const unsupportedGroupNotice = isGroupCall && (!groupCall?.capabilities.supportsGroupSignals || !groupCall.capabilities.supportsScreenShare || !groupCall.capabilities.supportsAudioMixer)
  const headerStatus = isGroupCall
    ? groupCall?.reconnecting
      ? 'Verbindung wird wiederhergestellt…'
      : groupCall?.errorMessage || `Live • ${formatSeconds(callDurationSeconds)}`
    : state === 'outgoing'
      ? 'Wähle...'
      : state === 'incoming'
        ? 'Eingehender Anruf...'
        : state === 'connecting'
          ? 'Verbindungsaufbau...'
          : state === 'active'
            ? formatSeconds(callDurationSeconds)
            : 'Bereit'

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/95 flex flex-col justify-between text-white select-none backdrop-blur-md animate-in fade-in duration-200">
      <div className="p-3 sm:p-4 flex items-center justify-between border-b border-white/10 bg-slate-900/40">
        <div className="flex items-center gap-2">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center ${isGroupCall ? 'bg-cyan-500/20 text-cyan-300' : 'bg-emerald-500/20 text-emerald-400'}`}>
            {isGroupCall ? <Users className="w-5 h-5" /> : <ShieldCheck className="w-5 h-5" />}
          </div>
          <div>
            <div className="flex items-center gap-1.5 font-semibold text-sm">
              <span>{isGroupCall ? groupCall?.name || 'Gruppenanruf' : partner?.username || 'Gesprächspartner'}</span>
              {isGroupCall ? (
                <span className="text-[10px] bg-cyan-500/20 text-cyan-200 px-1.5 py-0.5 rounded-full uppercase tracking-wider font-mono">
                  {groupParticipantCount} live
                </span>
              ) : (
                <span className="text-[10px] bg-emerald-500/20 text-emerald-300 px-1.5 py-0.5 rounded-full uppercase tracking-wider font-mono">
                  E2EE Active
                </span>
              )}
            </div>
            <div className="text-xs text-white/60">{headerStatus}</div>
          </div>
        </div>

        <Button
          variant="ghost"
          size="icon"
          onClick={() => setIsDeviceModalOpen(true)}
          className="text-white/80 hover:text-white hover:bg-white/10 rounded-full"
          title="Einstellungen"
        >
          <Settings className="w-5 h-5" />
        </Button>
      </div>

      <div className="flex-1 relative flex min-h-0 flex-col overflow-y-auto gap-3 p-3 sm:p-4 lg:flex-row lg:gap-4 lg:overflow-hidden">
        {isGroupCall ? (
          <>
            {unsupportedGroupNotice && (
              <div className="rounded-2xl border border-amber-400/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-50 shadow-inner shadow-amber-500/10">
                <div className="font-semibold uppercase tracking-[0.14em] text-amber-200">UI preview only</div>
                <p className="mt-1 text-amber-50/80">
                  {groupCall?.capabilities.reason ?? 'Gruppen-Signalisierung, Bildschirminhalte und Mixer-Funktionen werden in diesem Browser oder auf dieser Plattform noch nicht vollständig unterstützt.'}
                </p>
              </div>
            )}

            <div className="flex min-h-[min(52vh,34rem)] flex-1 min-w-0 flex-col gap-3 lg:min-h-0">
              <div className="flex shrink-0 items-center gap-2 overflow-x-auto pb-1 snap-x snap-mandatory" aria-label="Live screen share sources">
                <button
                  type="button"
                  onClick={() => setSelectedShareSource(null)}
                  className={`shrink-0 snap-start rounded-xl border px-3 py-2 text-left transition ${
                    groupCall!.selectedSourceId === null
                      ? 'border-violet-400/60 bg-violet-500/15 text-violet-100'
                      : 'border-white/10 bg-slate-900/60 text-white/70 hover:bg-slate-800'
                  }`}
                  aria-pressed={groupCall!.selectedSourceId === null}
                >
                  <div className="text-[10px] uppercase tracking-[0.16em] text-violet-200/70">Layout</div>
                  <div className="mt-1 text-sm font-medium">All live sources</div>
                </button>
                {groupCall?.screenSources.map((source) => (
                  <button
                    key={source.id}
                    type="button"
                    onClick={() => setSelectedShareSource(source.id)}
                    className={`shrink-0 rounded-xl border px-3 py-2 text-left transition ${
                      source.id === groupCall!.selectedSourceId
                        ? 'border-cyan-400/60 bg-cyan-500/15 text-cyan-100'
                        : 'border-white/10 bg-slate-900/60 text-white/70 hover:bg-slate-800'
                    }`}
                  >
                    <div className="text-[10px] uppercase tracking-[0.16em] text-white/55">{source.kind}</div>
                    <div className="text-sm font-medium mt-1">{source.label}</div>
                  </button>
                ))}
              </div>

              <div className="relative flex-1 min-h-0 overflow-hidden rounded-3xl border border-cyan-400/20 bg-gradient-to-br from-slate-900 via-slate-950 to-slate-900 shadow-2xl">
                <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(56,189,248,0.12),transparent_40%)]" />
                <div className="absolute left-3 top-3 sm:left-4 sm:top-4 flex items-center gap-2 rounded-full bg-slate-950/70 border border-white/10 px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-cyan-200">
                  <Monitor className="w-3 h-3" />
                  {selectedShareSource?.label ?? 'Stage focus'}
                </div>

                <div className="relative h-full w-full p-3 sm:p-4 flex items-center justify-center">
                  {groupCall!.selectedSourceId === null && groupCall!.screenSources.length > 0 ? (
                    <div className="grid h-full w-full auto-rows-fr grid-cols-1 gap-2 sm:grid-cols-2">
                      {groupCall!.screenSources.map((source) => (
                        <button
                          key={source.id}
                          type="button"
                          onClick={() => setSelectedShareSource(source.id)}
                          className="group rounded-2xl border border-violet-400/25 bg-slate-800/80 p-3 text-left transition hover:border-violet-300/60"
                        >
                          <div className="flex h-full min-h-28 flex-col justify-between rounded-xl bg-gradient-to-br from-violet-500/15 to-slate-950 p-3">
                            <span className="text-[10px] uppercase tracking-[0.15em] text-violet-200">{source.isLive ? 'Live' : 'Offline'} · {source.kind}</span>
                            <span className="text-sm font-semibold text-white">{source.ownerName} · {source.label}</span>
                          </div>
                        </button>
                      ))}
                    </div>
                  ) : (mode === 'video' || selectedShareSource) && !isCameraOff ? (
                    <div className="w-full h-full rounded-2xl overflow-hidden border border-white/10 bg-slate-800 relative">
                      {remoteStream ? (
                        <video ref={remoteVideoRef} autoPlay playsInline className="w-full h-full object-cover" />
                      ) : (
                        <div className="flex h-full items-center justify-center flex-col gap-3 text-white/55">
                          <LayoutGrid className="w-16 h-16 opacity-30" />
                          <p className="text-sm">Live stage ready</p>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-4 text-white/60">
                      <div className="w-24 h-24 rounded-full bg-cyan-500/15 border-2 border-cyan-400/35 flex items-center justify-center sm:w-28 sm:h-28">
                        <User className="w-12 h-12 sm:w-14 sm:h-14" />
                      </div>
                      <div className="text-center">
                        <div className="text-base font-semibold">Waiting for host signal</div>
                        <div className="text-xs text-white/50">Audio mix and share controls stay live</div>
                      </div>
                    </div>
                  )}

                  <div className="absolute bottom-3 right-3 h-28 w-24 overflow-hidden rounded-xl border-2 border-white/20 bg-slate-800 shadow-2xl sm:bottom-4 sm:right-4 sm:h-36 sm:w-28 lg:h-48 lg:w-36">
                    <video ref={localVideoRef} autoPlay playsInline muted className="h-full w-full object-cover -scale-x-100" />
                  </div>
                </div>
              </div>
            </div>

            <aside className="w-full shrink-0 rounded-3xl border border-white/10 bg-slate-900/70 p-3 sm:p-3 lg:w-[290px] lg:min-w-[290px]">
              <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.18em] text-white/55">
                <span>Participants</span>
                <button
                  type="button"
                  onClick={() => toggleAudioMixer()}
                  disabled={!canUseAudioMixer}
                  className="rounded-full border border-white/10 bg-slate-800 px-2 py-1 text-white/70 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                  title={
                    canUseAudioMixer
                      ? 'Audio-Mixer öffnen'
                      : 'Du kannst in dieser Gruppe keine Mixer-Steuerung öffnen.'
                  }
                >
                  {groupCall?.isAudioMixerOpen ? 'Hide mixer' : 'Mixer'}
                </button>
              </div>
              <div className="flex gap-2 overflow-x-auto pb-1 lg:block lg:space-y-2" aria-label="Participants">
                {(groupCall?.participants ?? []).map((participant) => (
                  <div
                    key={participant.userId}
                    className={`flex min-w-[210px] items-center gap-2 rounded-2xl border px-2.5 py-2 lg:min-w-0 ${
                      participant.isSpeaking ? 'border-cyan-400/35 bg-cyan-500/10' : 'border-white/10 bg-slate-950/40'
                    }`}
                  >
                    <div className="relative h-9 w-9 rounded-full bg-slate-700 overflow-hidden border border-white/10">
                      {participant.avatarUrl ? (
                        <img src={participant.avatarUrl} className="h-full w-full object-cover" alt={participant.username} />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-[10px] font-semibold text-white/75">
                          {participant.username.slice(0, 2).toUpperCase()}
                        </div>
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-white/90">{participant.username}</div>
                      <div className="text-[10px] text-white/50">{participant.isMuted ? 'Muted' : 'Live'}</div>
                    </div>
                    <div className="flex items-center gap-1.5 text-white/70">
                      {participant.isMuted ? <MicOff className="w-3.5 h-3.5" /> : <Mic className="w-3.5 h-3.5" />}
                      {participant.isCameraOff ? <VideoOff className="w-3.5 h-3.5" /> : <VideoIcon className="w-3.5 h-3.5" />}
                    </div>
                  </div>
                ))}
              </div>

              {groupCall?.isAudioMixerOpen && (
                <div className="rounded-2xl border border-cyan-400/20 bg-slate-950/60 p-3 space-y-3 text-white/75">
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.18em] text-cyan-200">
                    <span>Audio mix</span>
                    <Volume2 className="w-3.5 h-3.5" />
                  </div>

                  <label className="block">
                    <div className="mb-1 flex items-center justify-between text-xs">
                      <span>Master</span>
                      <span>{groupCall.audioMix.master}%</span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={groupCall.audioMix.master}
                      onChange={(e) => updateGroupAudioMix('master', 'master', Number(e.target.value))}
                      className="w-full accent-cyan-400"
                    />
                  </label>

                  {groupCall.screenSources.map((source) => (
                    <label key={`source-${source.id}`} className="block">
                      <div className="mb-1 flex items-center justify-between text-xs">
                        <span>{source.label}</span>
                        <span>{groupCall.audioMix.perSource[source.id] ?? 62}%</span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={groupCall.audioMix.perSource[source.id] ?? 62}
                        onChange={(e) => updateGroupAudioMix('source', source.id, Number(e.target.value))}
                        className="w-full accent-cyan-400"
                      />
                    </label>
                  ))}
                  {groupCall.participants.map((participant) => (
                    <label key={`participant-${participant.userId}`} className="block">
                      <div className="mb-1 flex items-center justify-between gap-2 text-xs">
                        <span className="truncate">{participant.username}</span>
                        <span>{groupCall.audioMix.perParticipant[participant.userId] ?? 65}%</span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={groupCall.audioMix.perParticipant[participant.userId] ?? 65}
                        onChange={(e) => updateGroupAudioMix('participant', participant.userId, Number(e.target.value))}
                        className="w-full accent-cyan-400"
                        aria-label={`${participant.username} Lautstärke`}
                      />
                    </label>
                  ))}
                  <button
                    type="button"
                    onClick={() => useCallStore.getState().muteAllGroupParticipants(!groupCall.audioMix.isMutedAll)}
                    className="w-full rounded-xl border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-left text-xs font-semibold text-rose-100 hover:bg-rose-500/20"
                  >
                    {groupCall.audioMix.isMutedAll ? 'Alle wieder einschalten' : 'Alle stummschalten'}
                  </button>
                  <p className="text-[10px] leading-relaxed text-white/50">
                    App-Audio-Mixing hängt von Browser- und Plattform-APIs ab und ist hier möglicherweise nicht verfügbar.
                  </p>
                </div>
              )}
            </aside>
          </>
        ) : (
          <div className="flex-1 relative flex items-center justify-center p-4 overflow-hidden">
            {mode === 'video' && !isCameraOff ? (
              <div className="w-full h-full relative flex items-center justify-center">
                <div className="w-full h-full max-w-4xl max-h-[75vh] bg-slate-900 rounded-2xl overflow-hidden relative shadow-2xl border border-white/10 flex items-center justify-center">
                  <video ref={remoteVideoRef} autoPlay playsInline className="w-full h-full object-cover" />
                  {!remoteStream && (
                    <div className="flex flex-col items-center gap-2 text-white/50">
                      <User className="w-16 h-16 opacity-30 animate-pulse" />
                      <span className="text-xs">Warte auf Videosignal...</span>
                    </div>
                  )}
                </div>

                <div className="absolute bottom-4 right-4 w-32 h-44 sm:w-44 sm:h-56 bg-slate-800 rounded-xl overflow-hidden shadow-2xl border-2 border-white/20">
                  <video ref={localVideoRef} autoPlay playsInline muted className="w-full h-full object-cover -scale-x-100" />
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-4">
                <div className="relative">
                  <div className="w-28 h-28 sm:w-36 sm:h-36 rounded-full bg-primary/20 border-2 border-primary/40 flex items-center justify-center text-primary shadow-xl">
                    {partner?.avatarUrl ? (
                      <img src={partner.avatarUrl} alt={partner.username} className="w-full h-full rounded-full object-cover" />
                    ) : (
                      <User className="w-14 h-14 sm:w-16 sm:h-16" />
                    )}
                  </div>
                  {state === 'active' && <div className="absolute inset-0 rounded-full border-2 border-primary/40 animate-ping pointer-events-none" />}
                </div>
                <div className="text-center">
                  <h3 className="font-bold text-lg">{partner?.username || 'Gesprächspartner'}</h3>
                  <p className="text-xs text-white/60">{state === 'active' ? 'Sprachanruf aktiv' : 'Rufe an...'}</p>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="sticky bottom-0 border-t border-white/10 bg-slate-900/80 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur-md sm:p-4">
        <div className="mx-auto flex max-w-lg flex-wrap items-center justify-center gap-2 sm:gap-4">
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleMute}
            className={`h-11 w-11 rounded-full ${isMuted ? 'bg-rose-500/20 text-rose-400 hover:bg-rose-500/30' : 'bg-white/10 text-white hover:bg-white/20'}`}
            title={isMuted ? 'Mikrofon einschalten' : 'Mikrofon stummschalten'}
            aria-label={isMuted ? 'Mikrofon einschalten' : 'Mikrofon stummschalten'}
          >
            {isMuted ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={() => {
              if (mode === 'audio') {
                setMode('video')
              } else {
                toggleCamera()
              }
            }}
            className={`h-11 w-11 rounded-full ${mode === 'audio' || isCameraOff ? 'bg-white/10 text-white/60 hover:bg-white/20' : 'bg-primary text-white hover:bg-primary/90'}`}
            title={mode === 'video' && !isCameraOff ? 'Kamera ausschalten' : 'Kamera einschalten'}
            aria-label={mode === 'video' && !isCameraOff ? 'Kamera ausschalten' : 'Kamera einschalten'}
          >
            {mode === 'video' && !isCameraOff ? <VideoIcon className="w-5 h-5" /> : <VideoOff className="w-5 h-5" />}
          </Button>

          <Button
            variant="ghost"
            size="icon"
            onClick={() => setScreenSharing(!isScreenSharing)}
            disabled={isGroupCall ? !canShareScreen : false}
            className={`h-11 w-11 rounded-full ${isScreenSharing ? 'bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30' : 'bg-white/10 text-white hover:bg-white/20'} ${isGroupCall && !canShareScreen ? 'opacity-60' : ''}`}
            title={
              isGroupCall
                ? canShareScreen
                  ? isScreenSharing
                    ? 'Freigabe beenden'
                    : 'Bildschirm teilen'
                  : !groupCall?.permissions.canShare
                    ? 'Du hast in dieser Gruppe keine Berechtigung, Bildschirminhalte freizugeben.'
                    : groupCall?.capabilities.reason ?? 'Bildschirmfreigabe wird in diesem Browser oder auf dieser Plattform nicht unterstützt.'
                : isScreenSharing
                  ? 'Freigabe beenden'
                  : 'Bildschirm teilen'
            }
            aria-label={isScreenSharing ? 'Freigabe beenden' : 'Bildschirm teilen'}
          >
            <Monitor className="w-5 h-5" />
          </Button>

          <Button
            variant="destructive"
            size="icon"
            onClick={endCall}
            className="h-11 w-11 rounded-full bg-rose-600 hover:bg-rose-700 text-white shadow-lg shadow-rose-600/30"
            title="Anruf beenden"
            aria-label="Anruf beenden"
          >
            <PhoneOff className="w-5 h-5" />
          </Button>
        </div>
      </div>

      <DeviceSelectorModal
        isOpen={isDeviceModalOpen}
        onClose={() => setIsDeviceModalOpen(false)}
        audioInputs={audioInputs}
        videoInputs={videoInputs}
        audioOutputs={audioOutputs}
        selectedAudioInput={selectedAudioInput}
        selectedVideoInput={selectedVideoInput}
        selectedAudioOutput={selectedAudioOutput}
        onSelectAudioInput={(id) => setDevices(id, undefined, undefined)}
        onSelectVideoInput={(id) => setDevices(undefined, id, undefined)}
        onSelectAudioOutput={(id) => setDevices(undefined, undefined, id)}
      />
    </div>
  )
}
