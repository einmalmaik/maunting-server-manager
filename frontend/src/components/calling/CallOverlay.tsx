import React, { useEffect, useRef, useState } from 'react'
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
  } = useCallStore()

  const [isDeviceModalOpen, setIsDeviceModalOpen] = useState(false)
  const [audioInputs, setAudioInputs] = useState<DropdownOption[]>([])
  const [videoInputs, setVideoInputs] = useState<DropdownOption[]>([])
  const [audioOutputs, setAudioOutputs] = useState<DropdownOption[]>([])

  const localVideoRef = useRef<HTMLVideoElement | null>(null)
  const remoteVideoRef = useRef<HTMLVideoElement | null>(null)

  // Timer interval for call duration
  useEffect(() => {
    if (state !== 'active') return
    const interval = setInterval(() => {
      incrementDuration()
    }, 1000)
    return () => clearInterval(interval)
  }, [state, incrementDuration])

  // Attach local stream to video element
  useEffect(() => {
    if (localVideoRef.current && localStream) {
      localVideoRef.current.srcObject = localStream
    }
  }, [localStream, mode, isCameraOff])

  // Attach remote stream to video element
  useEffect(() => {
    if (remoteVideoRef.current && remoteStream) {
      remoteVideoRef.current.srcObject = remoteStream
    }
  }, [remoteStream])

  // Enumerate devices on mount
  useEffect(() => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) return
    navigator.mediaDevices.enumerateDevices().then((devices) => {
      const aIn: DropdownOption[] = []
      const vIn: DropdownOption[] = []
      const aOut: DropdownOption[] = []

      devices.forEach((d, idx) => {
        if (d.kind === 'audioinput') {
          aIn.push({ value: d.deviceId || `mic-${idx}`, label: d.label || `Mikrofon ${idx + 1}` })
        } else if (d.kind === 'videoinput') {
          vIn.push({ value: d.deviceId || `cam-${idx}`, label: d.label || `Kamera ${idx + 1}` })
        } else if (d.kind === 'audiooutput') {
          aOut.push({ value: d.deviceId || `speaker-${idx}`, label: d.label || `Lautsprecher ${idx + 1}` })
        }
      })

      if (aIn.length) setAudioInputs(aIn)
      if (vIn.length) setVideoInputs(vIn)
      if (aOut.length) setAudioOutputs(aOut)
    }).catch(() => {})
  }, [])

  if (state === 'idle') return null

  const formatSeconds = (sec: number) => {
    const m = Math.floor(sec / 60)
    const s = sec % 60
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/95 flex flex-col justify-between text-white select-none backdrop-blur-md animate-in fade-in duration-200">
      {/* Header with E2EE & Call Status */}
      <div className="p-4 flex items-center justify-between border-b border-white/10 bg-slate-900/40">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-1.5 font-semibold text-sm">
              <span>{partner?.username || 'Gesprächspartner'}</span>
              <span className="text-[10px] bg-emerald-500/20 text-emerald-300 px-1.5 py-0.5 rounded-full uppercase tracking-wider font-mono">
                E2EE Active
              </span>
            </div>
            <div className="text-xs text-white/60">
              {state === 'outgoing' && 'Wähle...'}
              {state === 'incoming' && 'Eingehender Anruf...'}
              {state === 'connecting' && 'Verbindungsaufbau...'}
              {state === 'active' && formatSeconds(callDurationSeconds)}
            </div>
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

      {/* Main Video / Audio Grid Area */}
      <div className="flex-1 relative flex items-center justify-center p-4 overflow-hidden">
        {mode === 'video' && !isCameraOff ? (
          <div className="w-full h-full relative flex items-center justify-center">
            {/* Remote video (full size) */}
            <div className="w-full h-full max-w-4xl max-h-[75vh] bg-slate-900 rounded-2xl overflow-hidden relative shadow-2xl border border-white/10 flex items-center justify-center">
              <video
                ref={remoteVideoRef}
                autoPlay
                playsInline
                className="w-full h-full object-cover"
              />
              {!remoteStream && (
                <div className="flex flex-col items-center gap-2 text-white/50">
                  <User className="w-16 h-16 opacity-30 animate-pulse" />
                  <span className="text-xs">Warte auf Videosignal...</span>
                </div>
              )}
            </div>

            {/* Local picture-in-picture video */}
            <div className="absolute bottom-4 right-4 w-32 h-44 sm:w-44 sm:h-56 bg-slate-800 rounded-xl overflow-hidden shadow-2xl border-2 border-white/20">
              <video
                ref={localVideoRef}
                autoPlay
                playsInline
                muted
                className="w-full h-full object-cover -scale-x-100"
              />
            </div>
          </div>
        ) : (
          /* Audio-only avatar view */
          <div className="flex flex-col items-center gap-4">
            <div className="relative">
              <div className="w-28 h-28 sm:w-36 sm:h-36 rounded-full bg-primary/20 border-2 border-primary/40 flex items-center justify-center text-primary shadow-xl">
                {partner?.avatarUrl ? (
                  <img
                    src={partner.avatarUrl}
                    alt={partner.username}
                    className="w-full h-full rounded-full object-cover"
                  />
                ) : (
                  <User className="w-14 h-14 sm:w-16 sm:h-16" />
                )}
              </div>
              {state === 'active' && (
                <div className="absolute inset-0 rounded-full border-2 border-primary/40 animate-ping pointer-events-none" />
              )}
            </div>
            <div className="text-center">
              <h3 className="font-bold text-lg">{partner?.username || 'Gesprächspartner'}</h3>
              <p className="text-xs text-white/60">
                {state === 'active' ? 'Sprachanruf aktiv' : 'Rufe an...'}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Footer Controls */}
      <div className="p-6 flex items-center justify-center gap-4 bg-slate-900/60 border-t border-white/10 backdrop-blur-md">
        {/* Mute Toggle */}
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleMute}
          className={`w-12 h-12 rounded-full ${
            isMuted
              ? 'bg-rose-500/20 text-rose-400 hover:bg-rose-500/30'
              : 'bg-white/10 text-white hover:bg-white/20'
          }`}
          title={isMuted ? 'Mikrofon einschalten' : 'Mikrofon stummschalten'}
        >
          {isMuted ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
        </Button>

        {/* Video Toggle */}
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
          className={`w-12 h-12 rounded-full ${
            mode === 'audio' || isCameraOff
              ? 'bg-white/10 text-white/60 hover:bg-white/20'
              : 'bg-primary text-white hover:bg-primary/90'
          }`}
          title={mode === 'video' && !isCameraOff ? 'Kamera ausschalten' : 'Kamera einschalten'}
        >
          {mode === 'video' && !isCameraOff ? (
            <VideoIcon className="w-5 h-5" />
          ) : (
            <VideoOff className="w-5 h-5" />
          )}
        </Button>

        {/* Screen share toggle */}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setScreenSharing(!isScreenSharing)}
          className={`w-12 h-12 rounded-full ${
            isScreenSharing
              ? 'bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30'
              : 'bg-white/10 text-white hover:bg-white/20'
          }`}
          title={isScreenSharing ? 'Freigabe beenden' : 'Bildschirm teilen'}
        >
          <Monitor className="w-5 h-5" />
        </Button>

        {/* Hangup button */}
        <Button
          variant="destructive"
          size="icon"
          onClick={endCall}
          className="w-12 h-12 rounded-full bg-rose-600 hover:bg-rose-700 text-white shadow-lg shadow-rose-600/30"
          title="Anruf beenden"
        >
          <PhoneOff className="w-5 h-5" />
        </Button>
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
