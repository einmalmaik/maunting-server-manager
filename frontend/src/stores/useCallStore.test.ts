import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useCallStore } from './useCallStore'
import { getWebRtcIceServers, startDirectCall } from '@/api/social'
import { useToastStore } from '@/stores/toastStore'

vi.mock('@/api/social', () => ({
  startDirectCall: vi.fn(),
  cancelDirectCall: vi.fn().mockResolvedValue(undefined),
  endGroupCallRoom: vi.fn().mockResolvedValue(undefined),
  rejectDirectCall: vi.fn().mockResolvedValue(undefined),
  getWebRtcIceServers: vi.fn().mockResolvedValue({ ice_servers: [] }),
}))

class FakeSocket {
  static readonly OPEN = 1
  readonly readyState = FakeSocket.OPEN
  onopen: ((event: unknown) => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  sent: string[] = []
  send(payload: string) { this.sent.push(payload) }
  close() { this.onclose?.() }
}

class FakePeerConnection {
  onconnectionstatechange: (() => void) | null = null
  onicecandidate: ((event: { candidate: null }) => void) | null = null
  ontrack: ((event: { streams: unknown[] }) => void) | null = null
  connectionState = 'new'
  signalingState = 'stable'
  addTrack() {}
  close() {}
}

function installFakeCallTransport() {
  const stream = {
    getTracks: () => [] as MediaStreamTrack[],
    getAudioTracks: () => [] as MediaStreamTrack[],
    getVideoTracks: () => [] as MediaStreamTrack[],
  }
  Object.defineProperty(window.navigator, 'mediaDevices', {
    value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
    configurable: true,
  })
  vi.stubGlobal('RTCPeerConnection', FakePeerConnection)
  vi.stubGlobal('WebSocket', FakeSocket)
}

describe('useCallStore group calls', () => {
  beforeEach(() => {
    useCallStore.setState({
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
    })

  })

  it('starts a group call and selects the first share source by default', () => {
    const participants = [
      { userId: 1, username: 'me', isMuted: false, isCameraOff: false, isSpeaking: true, canModerate: true },
      { userId: 2, username: 'alice', isMuted: true, isCameraOff: false, isSpeaking: false, canModerate: false },
    ]

    useCallStore.getState().startGroupCall({
      id: 'group-1',
      name: 'Launch review',
      participants,
      screenSources: [
        { id: 'screen-1', ownerId: 1, ownerName: 'me', label: 'Desktop', kind: 'screen', isLive: true },
        { id: 'window-1', ownerId: 2, ownerName: 'alice', label: 'Browser', kind: 'window', isLive: true },
      ],
    })

    const groupCall = useCallStore.getState().groupCall
    expect(groupCall?.name).toBe('Launch review')
    expect(groupCall?.selectedSourceId).toBe('screen-1')
    expect(groupCall?.audioMix.perSource['screen-1']).toBe(62)
    expect(groupCall?.audioMix.perParticipant[2]).toBe(65)
    expect(useCallStore.getState().state).toBe('active')
  })

  it('updates master, source, and participant mixer values', () => {
    useCallStore.getState().startGroupCall({
      id: 'group-2',
      name: 'Q3 demo',
      participants: [{ userId: 9, username: 'nina' }],
      screenSources: [{ id: 'share-1', ownerId: 9, ownerName: 'nina', label: 'App', kind: 'app', isLive: true }],
    })

    useCallStore.getState().updateGroupAudioMix('master', 'master', 88)
    useCallStore.getState().updateGroupAudioMix('source', 'share-1', 77)
    useCallStore.getState().updateGroupAudioMix('participant', 9, 41)

    const groupCall = useCallStore.getState().groupCall
    expect(groupCall?.audioMix.master).toBe(88)
    expect(groupCall?.audioMix.perSource['share-1']).toBe(77)
    expect(groupCall?.audioMix.perParticipant[9]).toBe(41)
  })
})

describe('useCallStore direct calls', () => {
  beforeEach(() => {
    vi.mocked(startDirectCall).mockReset()
    vi.mocked(getWebRtcIceServers).mockResolvedValue({ ice_servers: [] })
    useToastStore.getState().clearAll()
    useCallStore.getState().endCall()
    installFakeCallTransport()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uses the backend-issued token for a friend call', async () => {
    vi.mocked(startDirectCall).mockResolvedValue({
      signaling_token: 'server-token-123456',
      recipient_id: 7,
      expires_in: 120,
    })

    const token = await useCallStore.getState().initiateCall(
      { userId: 7, username: 'Alice' },
      'video',
    )

    expect(startDirectCall).toHaveBeenCalledWith(7, 'video')
    expect(token).toBe('server-token-123456')
    expect(useCallStore.getState().blindToken).toBe('server-token-123456')
    expect(useCallStore.getState().state).toBe('outgoing')
  })

  it('ends the call with a visible error when the microphone is blocked', async () => {
    vi.mocked(startDirectCall).mockResolvedValue({
      signaling_token: 'server-token-blocked',
      recipient_id: 7,
      expires_in: 120,
    })
    const denied = Object.assign(new Error('denied'), { name: 'NotAllowedError' })
    vi.mocked(window.navigator.mediaDevices.getUserMedia).mockRejectedValue(denied)

    await useCallStore.getState().initiateCall(
      { userId: 7, username: 'Alice' },
      'audio',
    )

    expect(useCallStore.getState().state).toBe('idle')
    expect(useCallStore.getState().blindToken).toBeNull()
    expect(useToastStore.getState().toasts.some((t) => t.type === 'error')).toBe(true)
  })
})
