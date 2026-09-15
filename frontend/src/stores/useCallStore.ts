export type CallState = 'idle' | 'outgoing' | 'incoming' | 'connecting' | 'active' | 'ended'
export type CallMode = 'audio' | 'video'

export interface CallPartner {
  userId: number
  username: string
  avatarUrl?: string | null
}

export interface UseCallState {
  state: CallState
  mode: CallMode
  partner: CallPartner | null
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

  // Actions
  initiateCall: (partner: CallPartner, mode: CallMode) => Promise<string>
  receiveCall: (partner: CallPartner, mode: CallMode, blindToken: string) => void
  acceptCall: () => Promise<void>
  endCall: () => void
  toggleMute: () => void
  toggleCamera: () => void
  setMode: (mode: CallMode) => void
  setScreenSharing: (active: boolean) => void
  setDevices: (audioIn?: string, videoIn?: string, audioOut?: string) => void
  incrementDuration: () => void
}

import { create } from 'zustand'

export const useCallStore = create<UseCallState>((set, get) => ({
  state: 'idle',
  mode: 'audio',
  partner: null,
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
    const token = `blind_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`
    set({
      state: 'outgoing',
      partner,
      mode,
      blindToken: token,
      isMuted: false,
      isCameraOff: false,
      isScreenSharing: false,
      callDurationSeconds: 0,
    })
    return token
  },

  receiveCall: (partner, mode, blindToken) => {
    set({
      state: 'incoming',
      partner,
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
      blindToken: null,
      localStream: null,
      remoteStream: null,
      callDurationSeconds: 0,
      isScreenSharing: false,
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
  setScreenSharing: (active) => set({ isScreenSharing: active }),
  setDevices: (audioIn, videoIn, audioOut) => {
    set((s) => ({
      selectedAudioInput: audioIn ?? s.selectedAudioInput,
      selectedVideoInput: videoIn ?? s.selectedVideoInput,
      selectedAudioOutput: audioOut ?? s.selectedAudioOutput,
    }))
  },
  incrementDuration: () => set((s) => ({ callDurationSeconds: s.callDurationSeconds + 1 })),
}))
