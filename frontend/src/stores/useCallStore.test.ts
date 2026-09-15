import { beforeEach, describe, expect, it } from 'vitest'
import { useCallStore } from './useCallStore'

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
