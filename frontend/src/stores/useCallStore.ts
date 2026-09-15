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
import { startDirectCall } from '@/api/social'

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
    set({ state: 'connecting' })
  },

  endCall: () => {
    const { localStream } = get()
    if (localStream) {
      localStream.getTracks().forEach((t) => t.stop())
    }
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
