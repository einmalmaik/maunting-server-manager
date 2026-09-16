export type CallState = 'idle' | 'outgoing' | 'incoming' | 'connecting' | 'active' | 'ended'
export type CallMode = 'audio' | 'video'
export type ScreenShareKind = 'screen' | 'window' | 'app'

export interface CallPartner {
  userId: number
  username: string
  avatarUrl?: string | null
}

export interface CallParticipant {
  userId: number
  username: string
  avatarUrl?: string | null
  isMuted?: boolean
  isCameraOff?: boolean
  isSpeaking?: boolean
  canModerate?: boolean
}

export interface CallScreenShareSource {
  id: string
  ownerId: number
  ownerName: string
  label: string
  kind: ScreenShareKind
  isLive: boolean
}

export interface GroupCallAudioMix {
  master: number
  perSource: Record<string, number>
  perParticipant: Record<number, number>
  isMutedAll: boolean
}

export interface GroupCallPermissionState {
  canJoin: boolean
  canShare: boolean
  canModerate: boolean
}

export interface GroupCallCapabilityState {
  supportsGroupSignals: boolean
  supportsScreenShare: boolean
  supportsAudioMixer: boolean
  reason?: string | null
}

export interface GroupCallSession {
  id: string
  name: string
  participants: CallParticipant[]
  screenSources: CallScreenShareSource[]
  selectedSourceId: string | null
  isAudioMixerOpen: boolean
  audioMix: GroupCallAudioMix
  reconnecting: boolean
  errorMessage: string | null
  permissions: GroupCallPermissionState
  capabilities: GroupCallCapabilityState
}

export interface UseCallState {
  state: CallState
  mode: CallMode
  partner: CallPartner | null
  groupCall: GroupCallSession | null
  blindToken: string | null
  isMuted: boolean
  isCameraOff: boolean
  isScreenSharing: boolean
  selectedAudioInput: string
  selectedVideoInput: string
  selectedAudioOutput: string
  callDurationSeconds: number
  localStream: MediaStream | null
  remoteStream: MediaStream | null
  setLocalStream: (stream: MediaStream | null) => void
  setRemoteStream: (stream: MediaStream | null) => void

  initiateCall: (partner: CallPartner, mode: CallMode) => Promise<string>
  startGroupCall: (group: {
    id: string
    name: string
    participants?: CallParticipant[]
    screenSources?: CallScreenShareSource[]
    selectedSourceId?: string | null
    permissions?: GroupCallPermissionState
    capabilities?: GroupCallCapabilityState
    roomToken?: string
  }) => void
  receiveCall: (partner: CallPartner, mode: CallMode, blindToken: string) => void
  acceptCall: () => Promise<void>
  rejectCall: () => void
  endCall: () => void
  toggleMute: () => void
  toggleCamera: () => void
  setMode: (mode: CallMode) => void
  setScreenSharing: (active: boolean) => void
  setDevices: (audioIn?: string, videoIn?: string, audioOut?: string) => void
  incrementDuration: () => void
  setSelectedShareSource: (sourceId: string | null) => void
  toggleAudioMixer: (open?: boolean) => void
  updateGroupAudioMix: (target: 'master' | 'source' | 'participant', key: string | number, value: number) => void
  muteAllGroupParticipants: (muted?: boolean) => void
  joinGroupCall: (group: {
    id: string
    name: string
    participants?: CallParticipant[]
    screenSources?: CallScreenShareSource[]
    selectedSourceId?: string | null
    permissions?: GroupCallPermissionState
    capabilities?: GroupCallCapabilityState
    roomToken?: string
  }) => void
  joinExistingGroupCall: UseCallState['joinGroupCall']
}

import { create } from 'zustand'
import { cancelDirectCall, endGroupCallRoom, getWebRtcIceServers, rejectDirectCall, startDirectCall } from '@/api/social'
import { wsUrl } from '@/config/api'
import { toast } from '@/stores/toastStore'

let directSocket: WebSocket | null = null
let directPeer: RTCPeerConnection | null = null
let pendingCandidates: RTCIceCandidateInit[] = []
let transportGeneration = 0
let disconnectGraceTimer: ReturnType<typeof setTimeout> | null = null

function clearDisconnectGraceTimer() {
  if (disconnectGraceTimer !== null) {
    clearTimeout(disconnectGraceTimer)
    disconnectGraceTimer = null
  }
}

/** Erklärt Abbrüche beim Verbindungsaufbau, statt still aufzulegen. */
function meldeAnrufFehler(fehler: unknown, phase: 'mikrofon' | 'server') {
  const name = (fehler as { name?: string } | null)?.name ?? ''
  if (phase === 'mikrofon') {
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      toast.error('Mikrofon/Kamera blockiert. Bitte in den System- bzw. App-Einstellungen freigeben und erneut versuchen.')
    } else if (name === 'NotFoundError' || name === 'OverconstrainedError') {
      toast.error('Kein Mikrofon/Kamera gefunden. Bitte Gerät prüfen und erneut versuchen.')
    } else {
      toast.error('Mikrofon/Kamera konnte nicht geöffnet werden. Bitte erneut versuchen.')
    }
  } else {
    toast.error('Anruf-Server nicht erreichbar. Bitte Netzwerk prüfen und erneut versuchen.')
  }
}

function closeDirectTransport(sendLeave = true) {
  transportGeneration += 1
  pendingCandidates = []
  clearDisconnectGraceTimer()
  try {
    if (sendLeave && directSocket?.readyState === WebSocket.OPEN) {
      directSocket.send(JSON.stringify({ action: 'leave', reason: 'hangup' }))
    }
  } catch { /* ignore */ }
  directSocket?.close()
  directSocket = null
  directPeer?.close()
  directPeer = null
}

function sendSignal(payload: Record<string, unknown>) {
  if (directSocket?.readyState === WebSocket.OPEN) {
    directSocket.send(JSON.stringify({ action: 'signal', data: JSON.stringify(payload) }))
  }
}

async function connectDirectTransport(
  token: string,
  role: 'initiator' | 'receiver',
  mode: CallMode,
  set: (update: Partial<UseCallState>) => void,
  get: () => UseCallState,
) {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === 'undefined') {
    meldeAnrufFehler({ name: 'NotFoundError' }, 'mikrofon')
    throw new Error('media_unsupported')
  }
  closeDirectTransport()
  const generation = transportGeneration
  const ice = await (typeof getWebRtcIceServers === 'function'
    ? getWebRtcIceServers().catch(() => ({ ice_servers: [] as RTCIceServer[] }))
    : Promise.resolve({ ice_servers: [] as RTCIceServer[] }))
  if (generation !== transportGeneration) return
  const peer = new RTCPeerConnection({ iceServers: ice.ice_servers })
  directPeer = peer
  let local: MediaStream
  try {
    local = await navigator.mediaDevices.getUserMedia({ audio: true, video: mode === 'video' })
  } catch (fehler) {
    if (generation === transportGeneration) meldeAnrufFehler(fehler, 'mikrofon')
    peer.close()
    throw fehler
  }
  if (generation !== transportGeneration) {
    local.getTracks().forEach((track) => track.stop())
    peer.close()
    return
  }
  set({ localStream: local })
  local.getTracks().forEach((track) => peer.addTrack(track, local))
  peer.ontrack = (event) => {
    if (event.streams[0]) set({ remoteStream: event.streams[0], state: 'active' })
  }
  peer.onicecandidate = (event) => {
    if (event.candidate) sendSignal({ type: 'ice-candidate', candidate: event.candidate.toJSON() })
  }
  peer.onconnectionstatechange = () => {
    if (generation !== transportGeneration) return
    if (peer.connectionState === 'connected') {
      clearDisconnectGraceTimer()
      set({ state: 'active' })
      return
    }
    // Mobilfunknetze verlieren ICE kurzzeitig — nicht sofort auflegen,
    // sondern erst nach einer Gnadenfrist ohne Wiederverbindung.
    if (peer.connectionState === 'disconnected') {
      if (disconnectGraceTimer === null && get().state !== 'idle') {
        disconnectGraceTimer = setTimeout(() => {
          disconnectGraceTimer = null
          if (generation === transportGeneration && get().state !== 'idle') {
            toast.error('Verbindung abgebrochen. Bitte erneut versuchen.')
            get().endCall()
          }
        }, 8000)
      }
      return
    }
    if (peer.connectionState === 'failed' && get().state !== 'idle') {
      toast.error('Keine direkte Verbindung möglich (NAT/Firewall). Bitte erneut versuchen.')
      get().endCall()
      return
    }
    if (peer.connectionState === 'closed' && get().state !== 'idle') get().endCall()
  }

  const socket = new WebSocket(wsUrl('/api/social/webrtc/signal'))
  directSocket = socket
  const sendOffer = async () => {
    if (role !== 'initiator' || peer.signalingState !== 'stable') return
    const offer = await peer.createOffer()
    await peer.setLocalDescription(offer)
    sendSignal({ type: 'offer', sdp: offer.sdp })
  }
  socket.onopen = () => socket.send(JSON.stringify({ action: 'join', token }))
  socket.onmessage = async (event) => {
    if (generation !== transportGeneration) return
    let message: { event?: string; data?: string; peer_count?: number }
    try { message = JSON.parse(String(event.data)) } catch { return }
    if (message.event === 'joined') {
      // Initiator joined second while the receiver was already waiting:
      // no peer_joined will arrive for us, so offer directly.
      if (typeof message.peer_count === 'number' && message.peer_count >= 2) {
        await sendOffer().catch(() => {})
      }
      return
    }
    if (message.event === 'peer_joined') {
      await sendOffer().catch(() => {})
      return
    }
    if (message.event === 'peer_left') {
      get().endCall()
      return
    }
    if (message.event !== 'signal' || !message.data) return
    let signal: { type?: string; sdp?: string; candidate?: RTCIceCandidateInit }
    try { signal = JSON.parse(message.data) } catch { return }
    if (signal.type === 'offer' && signal.sdp) {
      await peer.setRemoteDescription({ type: 'offer', sdp: signal.sdp })
      for (const candidate of pendingCandidates) await peer.addIceCandidate(candidate).catch(() => {})
      pendingCandidates = []
      const answer = await peer.createAnswer()
      await peer.setLocalDescription(answer)
      sendSignal({ type: 'answer', sdp: answer.sdp })
    } else if (signal.type === 'answer' && signal.sdp) {
      await peer.setRemoteDescription({ type: 'answer', sdp: signal.sdp })
      for (const candidate of pendingCandidates) await peer.addIceCandidate(candidate).catch(() => {})
      pendingCandidates = []
    } else if (signal.type === 'ice-candidate' && signal.candidate) {
      if (peer.remoteDescription) await peer.addIceCandidate(signal.candidate).catch(() => {})
      else pendingCandidates.push(signal.candidate)
    }
  }
  let joinedOk = false
  const prevOnMessage = socket.onmessage
  socket.onmessage = async (event) => {
    try {
      if (String(event.data).includes('"joined"')) joinedOk = true
    } catch { /* ignore */ }
    await prevOnMessage?.call(socket, event)
  }
  socket.onclose = () => {
    if (generation !== transportGeneration || get().state === 'idle') return
    // Socket starb vor dem Join (z. B. Server nicht erreichbar): Grund nennen,
    // statt das Overlay wortlos zu schließen.
    if (!joinedOk) meldeAnrufFehler(null, 'server')
    get().endCall()
  }
}

const DEFAULT_GROUP_AUDIO_MIX: GroupCallAudioMix = {
  master: 72,
  perSource: {},
  perParticipant: {},
  isMutedAll: false,
}

export const useCallStore = create<UseCallState>((set, get) => ({
  state: 'idle',
  mode: 'audio',
  partner: null,
  groupCall: null,
  blindToken: null,
  isMuted: false,
  isCameraOff: false,
  isScreenSharing: false,
  selectedAudioInput: 'default',
  selectedVideoInput: 'default',
  selectedAudioOutput: 'default',
  callDurationSeconds: 0,
  localStream: null,
  remoteStream: null,
  setLocalStream: (stream) => set({ localStream: stream }),
  setRemoteStream: (stream) => set({ remoteStream: stream }),

  initiateCall: async (partner, mode) => {
    const { signaling_token: token } = await startDirectCall(partner.userId, mode)
    set({
      state: 'outgoing',
      partner,
      groupCall: null,
      mode,
      blindToken: token,
      isMuted: false,
      isCameraOff: false,
      isScreenSharing: false,
      callDurationSeconds: 0,
    })
    await connectDirectTransport(token, 'initiator', mode, set, get).catch(() => {
      if (get().state !== 'idle') get().endCall()
    })
    return token
  },

  startGroupCall: (group) => {
    get().joinGroupCall(group)
  },

  joinGroupCall: (group) => {
    const screenSources = group.screenSources ?? []
    const selectedSourceId = group.selectedSourceId ?? (screenSources[0]?.id ?? null)
    const permissions = group.permissions ?? {
      canJoin: true,
      canShare: true,
      canModerate: true,
    }
    const capabilities = group.capabilities ?? {
      supportsGroupSignals: true,
      supportsScreenShare: true,
      supportsAudioMixer: true,
      reason: null,
    }

    set({
      state: 'active',
      partner: null,
      mode: 'video',
      // Group rooms must be created by the backend; never synthesize a token.
      blindToken: group.roomToken ?? null,
      groupCall: {
        id: group.id,
        name: group.name,
        participants: group.participants ?? [],
        screenSources,
        selectedSourceId,
        isAudioMixerOpen: false,
        reconnecting: false,
        errorMessage: !capabilities.supportsGroupSignals ? capabilities.reason ?? 'Gruppen-Signalisierung wird in diesem Browser oder auf dieser Plattform nicht unterstützt.' : null,
        audioMix: {
          master: DEFAULT_GROUP_AUDIO_MIX.master,
          perSource: Object.fromEntries(screenSources.map((src) => [src.id, 62])),
          perParticipant: Object.fromEntries((group.participants ?? []).map((p) => [p.userId, 65])),
          isMutedAll: false,
        },
        permissions,
        capabilities,
      },
      isMuted: false,
      isCameraOff: false,
      isScreenSharing: screenSources.length > 0 && capabilities.supportsScreenShare && permissions.canShare,
      callDurationSeconds: 0,
    })
  },

  joinExistingGroupCall: (group) => {
    get().joinGroupCall(group)
  },

  receiveCall: (partner, mode, blindToken) => {
    closeDirectTransport()
    set({
      state: 'incoming',
      partner,
      groupCall: null,
      mode,
      blindToken,
      callDurationSeconds: 0,
    })
  },

  acceptCall: async () => {
    const { blindToken, mode } = get()
    if (!blindToken) return
    set({ state: 'connecting' })
    await connectDirectTransport(blindToken, 'receiver', mode, set, get).catch(() => get().endCall())
  },

  rejectCall: () => {
    const token = get().blindToken
    if (token) void rejectDirectCall(token).catch(() => {})
    get().endCall()
  },
  endCall: () => {
    const { localStream, blindToken, state, groupCall } = get()
    // Caller hangs up while ringing: notify the recipient server-side so the
    // incoming overlay can be dismissed instead of ringing forever.
    if (blindToken && state === 'outgoing' && !groupCall) {
      void cancelDirectCall(blindToken).catch(() => {})
    }
    if (groupCall && blindToken) {
      const groupId = Number(groupCall.id)
      if (Number.isFinite(groupId)) {
        void endGroupCallRoom(groupId, blindToken).catch(() => {})
      }
    }
    if (localStream) {
      localStream.getTracks().forEach((t) => t.stop())
    }
    get().remoteStream?.getTracks().forEach((t) => t.stop())
    closeDirectTransport()
    set({
      state: 'idle',
      partner: null,
      groupCall: null,
      blindToken: null,
      localStream: null,
      remoteStream: null,
      callDurationSeconds: 0,
      isScreenSharing: false,
      mode: 'audio',
    })
  },

  toggleMute: () => {
    const { localStream, isMuted } = get()
    const next = !isMuted
    if (localStream) {
      localStream.getAudioTracks().forEach((t) => {
        t.enabled = !next
      })
    }
    set({ isMuted: next })
  },

  toggleCamera: () => {
    const { localStream, isCameraOff } = get()
    const next = !isCameraOff
    if (localStream) {
      localStream.getVideoTracks().forEach((t) => {
        t.enabled = !next
      })
    }
    set({ isCameraOff: next })
  },

  setMode: (mode) => set({ mode }),
  setScreenSharing: (active) => {
    set((state) => {
      if (!state.groupCall) {
        return { isScreenSharing: active }
      }

      const canShare = state.groupCall.permissions.canShare && state.groupCall.capabilities.supportsScreenShare
      if (active && !canShare) {
        return {
          ...state,
          isScreenSharing: false,
          groupCall: {
            ...state.groupCall,
            errorMessage:
              !state.groupCall.permissions.canShare
                ? 'Du hast in dieser Gruppe keine Berechtigung, Bildschirminhalte freizugeben.'
                : state.groupCall.capabilities.reason ?? 'Bildschirmfreigabe wird in diesem Browser oder auf dieser Plattform nicht unterstützt.',
          },
        }
      }

      return {
        ...state,
        isScreenSharing: active,
        groupCall: state.groupCall ? { ...state.groupCall, errorMessage: active ? state.groupCall.errorMessage : null } : null,
      }
    })
  },
  setDevices: (audioIn, videoIn, audioOut) => {
    set((s) => ({
      selectedAudioInput: audioIn ?? s.selectedAudioInput,
      selectedVideoInput: videoIn ?? s.selectedVideoInput,
      selectedAudioOutput: audioOut ?? s.selectedAudioOutput,
    }))
  },
  incrementDuration: () => set((s) => ({ callDurationSeconds: s.callDurationSeconds + 1 })),
  setSelectedShareSource: (sourceId) => {
    set((s) => ({
      groupCall: s.groupCall ? { ...s.groupCall, selectedSourceId: sourceId } : null,
    }))
  },
  toggleAudioMixer: (open) => {
    set((s) => {
      if (!s.groupCall) return s
      const canUseMixer = s.groupCall.permissions.canModerate
      if (!canUseMixer) {
        return {
          ...s,
          groupCall: {
            ...s.groupCall,
            isAudioMixerOpen: false,
            errorMessage: 'Die Mixer-Steuerung ist für deine Rolle in dieser Gruppe nicht verfügbar.',
          },
        }
      }

      return {
        ...s,
        groupCall: {
          ...s.groupCall,
          isAudioMixerOpen: open ?? !s.groupCall.isAudioMixerOpen,
          errorMessage: s.groupCall.capabilities.supportsAudioMixer
            ? null
            : s.groupCall.capabilities.reason ?? 'Audio-Mixer ist in diesem Browser oder auf dieser Plattform nicht unterstützt.',
        },
      }
    })
  },
  updateGroupAudioMix: (target, key, value) => {
    set((s) => {
      if (!s.groupCall) return s
      const nextMix = { ...s.groupCall.audioMix }
      if (target === 'master') {
        nextMix.master = value
      } else if (target === 'source') {
        nextMix.perSource = { ...nextMix.perSource, [String(key)]: value }
      } else {
        nextMix.perParticipant = { ...nextMix.perParticipant, [Number(key)]: value }
      }
      return { groupCall: { ...s.groupCall, audioMix: nextMix } }
    })
  },
  muteAllGroupParticipants: (muted = true) => {
    set((s) => {
      if (!s.groupCall) return s
      return {
        groupCall: {
          ...s.groupCall,
          audioMix: { ...s.groupCall.audioMix, isMutedAll: muted },
          participants: s.groupCall.participants.map((participant) => ({
            ...participant,
            isMuted: muted,
          })),
        },
      }
    })
  },
}))
