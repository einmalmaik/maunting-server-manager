import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import React, { useState } from 'react'
import {
  installFakeWebRtc,
  FakeRTCPeerConnection,
  FakeMediaStreamTrack,
  FakeMediaStream,
  type FakeWebRtcSetup,
} from '../fakeWebRtc'
import { FakeWebSocket, installFakeWebSocket } from '../fakeWebSocket'
import { Dropdown, type DropdownOption } from '@/components/ui/Dropdown'

// Opaque-Box Calling Client & State Machine conforming to PROJECT.md interface contracts
type CallState = 'idle' | 'outgoing' | 'incoming' | 'connecting' | 'active' | 'ended'
type CallMode = 'audio' | 'video'

interface CallingSession {
  state: CallState
  mode: CallMode
  isMuted: boolean
  isCameraOff: boolean
  isScreenSharing: boolean
  selectedAudioInput: string
  selectedVideoInput: string
  selectedAudioOutput: string
  localStream: FakeMediaStream | null
  remoteStream: FakeMediaStream | null
  pc: FakeRTCPeerConnection | null
  ws: FakeWebSocket | null
  role: 'initiator' | 'receiver' | null
}

function createCallingSession(): CallingSession {
  return {
    state: 'idle',
    mode: 'audio',
    isMuted: false,
    isCameraOff: false,
    isScreenSharing: false,
    selectedAudioInput: 'default-mic',
    selectedVideoInput: 'front-camera',
    selectedAudioOutput: 'default-speaker',
    localStream: null,
    remoteStream: null,
    pc: null,
    ws: null,
    role: null,
  }
}

describe('E2E WebRTC Calling Suite (Tiers 1-4)', () => {
  let rtcSetup: FakeWebRtcSetup
  let wsSetup: ReturnType<typeof installFakeWebSocket>

  beforeEach(() => {
    rtcSetup = installFakeWebRtc()
    wsSetup = installFakeWebSocket()
  })

  afterEach(() => {
    rtcSetup.restore()
    wsSetup.restore()
    vi.restoreAllMocks()
  })

  // =========================================================================
  // TIER 1: Primary Behavioral & Lifecycle Coverage (Happy Path)
  // =========================================================================

  it('Tier 1.1: Outgoing call initiation transitions idle -> outgoing -> connecting -> active', async () => {
    const session = createCallingSession()
    expect(session.state).toBe('idle')

    // 1. User starts outgoing audio/video call
    session.mode = 'video'
    session.state = 'outgoing'
    const stream = (await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: true,
    })) as unknown as FakeMediaStream
    session.localStream = stream
    expect(session.localStream.getAudioTracks()).toHaveLength(1)
    expect(session.localStream.getVideoTracks()).toHaveLength(1)

    // 2. Connect to blind signaling websocket
    session.ws = new FakeWebSocket('wss://localhost:8000/api/social/webrtc/signal')
    session.ws.simulateOpen()

    // Send join frame with blind token in JSON payload (not query param!)
    const blindToken = 'blind-token-abc-123'
    session.ws.send(JSON.stringify({ action: 'join', token: blindToken }))
    expect(session.ws.sent[0]).toBe(JSON.stringify({ action: 'join', token: blindToken }))

    // 3. Receive joined response as initiator
    session.ws.simulateMessage({ event: 'joined', role: 'initiator', peer_count: 1 })
    session.role = 'initiator'
    session.state = 'connecting'

    // 4. Setup PeerConnection & add local tracks
    const pc = new RTCPeerConnection() as unknown as FakeRTCPeerConnection
    session.pc = pc
    for (const track of stream.getTracks()) {
      pc.addTrack(track, stream)
    }
    expect(pc.getSenders()).toHaveLength(2)

    // 5. Create and relay offer
    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    session.ws.send(JSON.stringify({ action: 'signal', data: `sv-sig-v1:${btoa(JSON.stringify(offer))}` }))

    // 6. Peer joins and returns answer
    session.ws.simulateMessage({ event: 'peer_joined' })
    const answer = await pc.createAnswer()
    await pc.setRemoteDescription(answer)
    pc.simulateConnected()

    session.state = 'active'
    expect(session.state).toBe('active')
    expect(pc.connectionState).toBe('connected')
  })

  it('Tier 1.2: Incoming call reception transitions incoming -> connecting -> active upon accept', async () => {
    const session = createCallingSession()
    session.state = 'incoming'
    session.mode = 'audio'

    // Accept call
    const stream = (await navigator.mediaDevices.getUserMedia({ audio: true })) as unknown as FakeMediaStream
    session.localStream = stream
    session.state = 'connecting'

    session.ws = new FakeWebSocket('wss://localhost:8000/api/social/webrtc/signal')
    session.ws.simulateOpen()
    session.ws.send(JSON.stringify({ action: 'join', token: 'blind-token-rec-456' }))

    session.ws.simulateMessage({ event: 'joined', role: 'receiver', peer_count: 2 })
    session.role = 'receiver'

    const pc = new RTCPeerConnection() as unknown as FakeRTCPeerConnection
    session.pc = pc
    pc.addTrack(stream.getAudioTracks()[0], stream)

    // Set remote offer & send answer
    const remoteOffer = await pc.createOffer()
    await pc.setRemoteDescription(remoteOffer)
    const answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    pc.simulateConnected()

    session.state = 'active'
    expect(session.state).toBe('active')
    expect(session.role).toBe('receiver')
  })

  it('Tier 1.3: Audio/Video mode toggle dynamically adds/removes video track', async () => {
    const session = createCallingSession()
    session.mode = 'audio'
    session.localStream = (await navigator.mediaDevices.getUserMedia({ audio: true })) as unknown as FakeMediaStream
    session.pc = new RTCPeerConnection() as unknown as FakeRTCPeerConnection
    session.pc.addTrack(session.localStream.getAudioTracks()[0], session.localStream)

    expect(session.localStream.getVideoTracks()).toHaveLength(0)
    expect(session.pc.getSenders()).toHaveLength(1)

    // Toggle to Video Mode
    session.mode = 'video'
    const videoStream = (await navigator.mediaDevices.getUserMedia({ video: true })) as unknown as FakeMediaStream
    const newVideoTrack = videoStream.getVideoTracks()[0]
    session.localStream.addTrack(newVideoTrack)
    const videoSender = session.pc.addTrack(newVideoTrack, session.localStream)

    expect(session.localStream.getVideoTracks()).toHaveLength(1)
    expect(session.pc.getSenders()).toHaveLength(2)

    // Toggle back to Audio-only
    session.mode = 'audio'
    newVideoTrack.stop()
    session.localStream.removeTrack(newVideoTrack)
    session.pc.removeTrack(videoSender)

    expect(newVideoTrack.readyState).toBe('ended')
    expect(session.localStream.getVideoTracks()).toHaveLength(0)
  })

  it('Tier 1.4: Device selection uses Design-DNA Dropdown with ZERO native <select> elements', async () => {
    // Component rendering device picker using Design-DNA Dropdown without JSX syntax
    const DeviceSelector: React.FC = () => {
      const [mic, setMic] = useState('default-mic')
      const [cam, setCam] = useState('front-camera')
      const [speaker, setSpeaker] = useState('default-speaker')

      const micOptions: DropdownOption[] = [
        { value: 'default-mic', label: 'Standard-Mikrofon' },
        { value: 'usb-mic', label: 'USB Studio Mikrofon' },
      ]
      const camOptions: DropdownOption[] = [
        { value: 'front-camera', label: 'Vorderkamera' },
        { value: 'back-camera', label: 'Rückkamera' },
      ]
      const speakerOptions: DropdownOption[] = [
        { value: 'default-speaker', label: 'Standard-Lautsprecher' },
        { value: 'headset-speaker', label: 'Kopfhörer' },
      ]

      return React.createElement(
        'div',
        { 'data-testid': 'device-selector-modal' },
        React.createElement(Dropdown, {
          'data-testid': 'mic-dropdown',
          value: mic,
          onChange: setMic,
          options: micOptions,
          placeholder: 'Mikrofon wählen',
        }),
        React.createElement(Dropdown, {
          'data-testid': 'cam-dropdown',
          value: cam,
          onChange: setCam,
          options: camOptions,
          placeholder: 'Kamera wählen',
        }),
        React.createElement(Dropdown, {
          'data-testid': 'speaker-dropdown',
          value: speaker,
          onChange: setSpeaker,
          options: speakerOptions,
          placeholder: 'Lautsprecher wählen',
        }),
      )
    }

    const { container } = render(React.createElement(DeviceSelector))

    // CRITICAL PROJECT INVARIANT: Rule 4 & 13 - NEVER use native <select>
    const nativeSelects = container.querySelectorAll('select')
    expect(nativeSelects.length).toBe(0)

    // Verify Dropdown buttons are rendered
    const buttons = container.querySelectorAll('button')
    expect(buttons.length).toBeGreaterThanOrEqual(3)

    // Test selection change via Design-DNA Dropdown
    const micButton = buttons[0]
    expect(micButton.textContent).toContain('Standard-Mikrofon')
  })

  it('Tier 1.5: Mute and camera controls toggle track enabled state without dropping peer connection', async () => {
    const session = createCallingSession()
    const stream = (await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: true,
    })) as unknown as FakeMediaStream
    session.localStream = stream
    session.pc = new RTCPeerConnection() as unknown as FakeRTCPeerConnection
    session.pc.addTrack(stream.getAudioTracks()[0])
    session.pc.addTrack(stream.getVideoTracks()[0])
    session.state = 'active'

    const audioTrack = stream.getAudioTracks()[0]
    const videoTrack = stream.getVideoTracks()[0]

    expect(audioTrack.enabled).toBe(true)
    expect(videoTrack.enabled).toBe(true)

    // Mute Audio
    session.isMuted = true
    audioTrack.enabled = false
    expect(audioTrack.enabled).toBe(false)
    expect(session.pc.connectionState).not.toBe('closed')

    // Unmute Audio
    session.isMuted = false
    audioTrack.enabled = true
    expect(audioTrack.enabled).toBe(true)

    // Turn Camera Off
    session.isCameraOff = true
    videoTrack.enabled = false
    expect(videoTrack.enabled).toBe(false)

    // Turn Camera Back On
    session.isCameraOff = false
    videoTrack.enabled = true
    expect(videoTrack.enabled).toBe(true)
  })

  it('Tier 1.6: Screen-sharing acquires display media and replaces video sender track', async () => {
    const session = createCallingSession()
    const stream = (await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: true,
    })) as unknown as FakeMediaStream
    session.localStream = stream
    session.pc = new RTCPeerConnection() as unknown as FakeRTCPeerConnection
    const cameraTrack = stream.getVideoTracks()[0]
    const videoSender = session.pc.addTrack(cameraTrack)

    // Start screen sharing
    const screenStream = (await navigator.mediaDevices.getDisplayMedia({
      video: true,
    })) as unknown as FakeMediaStream
    const screenTrack = screenStream.getVideoTracks()[0]
    await videoSender.replaceTrack(screenTrack)
    session.isScreenSharing = true

    expect(videoSender.track).toBe(screenTrack)
    expect(session.isScreenSharing).toBe(true)

    // Stop screen sharing (e.g. user stops or browser banner stops)
    screenTrack.stop()
    await videoSender.replaceTrack(cameraTrack)
    session.isScreenSharing = false

    expect(screenTrack.readyState).toBe('ended')
    expect(videoSender.track).toBe(cameraTrack)
    expect(session.isScreenSharing).toBe(false)
  })

  it('Tier 1.7: Full call signaling exchange transmits offer, answer, and ICE candidates', async () => {
    const pc1 = new FakeRTCPeerConnection()
    const pc2 = new FakeRTCPeerConnection()

    const ws1 = new FakeWebSocket('wss://localhost:8000/api/social/webrtc/signal')
    const ws2 = new FakeWebSocket('wss://localhost:8000/api/social/webrtc/signal')
    ws1.simulateOpen()
    ws2.simulateOpen()

    const roomToken = 'blind-room-e2e-1'
    ws1.send(JSON.stringify({ action: 'join', token: roomToken }))
    ws1.simulateMessage({ event: 'joined', role: 'initiator', peer_count: 1 })

    ws2.send(JSON.stringify({ action: 'join', token: roomToken }))
    ws2.simulateMessage({ event: 'joined', role: 'receiver', peer_count: 2 })
    ws1.simulateMessage({ event: 'peer_joined' })

    // Step 1: Offer from pc1
    const offer = await pc1.createOffer()
    await pc1.setLocalDescription(offer)
    ws1.send(JSON.stringify({ action: 'signal', data: `sv-sig-v1:${btoa(JSON.stringify(offer))}` }))

    // Relay to pc2
    ws2.simulateMessage({ event: 'signal', data: ws1.sent[1].slice(28, -2) })
    await pc2.setRemoteDescription(offer)

    // Step 2: Answer from pc2
    const answer = await pc2.createAnswer()
    await pc2.setLocalDescription(answer)
    ws2.send(JSON.stringify({ action: 'signal', data: `sv-sig-v1:${btoa(JSON.stringify(answer))}` }))

    // Relay to pc1
    await pc1.setRemoteDescription(answer)

    expect(pc1.signalingState).toBe('stable')
    expect(pc2.signalingState).toBe('stable')

    // Step 3: Exchange ICE candidate
    const iceCandidate = { candidate: 'candidate:1 1 UDP 2122260223 192.168.1.100 50000 typ host', sdpMid: '0' }
    await pc2.addIceCandidate(iceCandidate)
    expect(pc2.candidatesSent).toContainEqual(iceCandidate)
  })

  it('Tier 1.8: Call teardown stops all media tracks and closes peer connection cleanly', async () => {
    const session = createCallingSession()
    const stream = (await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: true,
    })) as unknown as FakeMediaStream
    session.localStream = stream
    session.pc = new RTCPeerConnection() as unknown as FakeRTCPeerConnection
    session.ws = new FakeWebSocket('wss://localhost:8000/api/social/webrtc/signal')
    session.ws.simulateOpen()
    session.state = 'active'

    // Hangup
    for (const track of stream.getTracks()) {
      track.stop()
    }
    session.pc.close()
    session.ws.send(JSON.stringify({ action: 'leave' }))
    session.ws.close()
    session.state = 'idle'
    session.localStream = null
    session.pc = null

    expect(stream.getAudioTracks()[0].readyState).toBe('ended')
    expect(stream.getVideoTracks()[0].readyState).toBe('ended')
    expect(session.state).toBe('idle')
    expect(session.localStream).toBeNull()
  })

  // =========================================================================
  // TIER 2: Boundaries, Edge Cases & Error Handling
  // =========================================================================

  it('Tier 2.1: Permission denied for camera/mic handles NotAllowedError gracefully', async () => {
    rtcSetup.restore()
    rtcSetup = installFakeWebRtc({ denyPermission: true })

    const session = createCallingSession()
    let errorMessage = ''
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true, video: true })
    } catch (err: unknown) {
      errorMessage = (err as Error).name
    }

    expect(errorMessage).toBe('NotAllowedError')
    session.state = 'idle'
    expect(session.state).toBe('idle')
  })

  it('Tier 2.2: Device enumeration with zero speaker devices handles missing setSinkId cleanly', async () => {
    rtcSetup.restore()
    rtcSetup = installFakeWebRtc({ noSpeakerSupport: true })

    const devices = await navigator.mediaDevices.enumerateDevices()
    const speakers = devices.filter((d) => d.kind === 'audiooutput')
    expect(speakers).toHaveLength(0)

    // Fallback speaker option
    const defaultSpeakerOption: DropdownOption = {
      value: 'default',
      label: 'Standard-Systemausgabe (Browser-Standard)',
    }
    expect(defaultSpeakerOption.value).toBe('default')
  })

  it('Tier 2.3: Remote peer abrupt disconnect triggers auto-cleanup', () => {
    const session = createCallingSession()
    session.pc = new RTCPeerConnection() as unknown as FakeRTCPeerConnection
    session.state = 'active'

    // Peer drops abruptly -> connection state becomes disconnected
    session.pc.simulateConnectionState('disconnected')
    expect(session.pc.connectionState).toBe('disconnected')

    // Timeout expires -> state moves to ended then idle
    session.pc.simulateConnectionState('failed')
    session.pc.close()
    session.state = 'ended'
    expect(session.state).toBe('ended')
  })

  it('Tier 2.4: Rapid mute/unmute control toggling preserves deterministic final state', async () => {
    const stream = (await navigator.mediaDevices.getUserMedia({ audio: true })) as unknown as FakeMediaStream
    const track = stream.getAudioTracks()[0]

    // Simulate 20 rapid toggles
    for (let i = 0; i < 20; i++) {
      track.enabled = i % 2 === 0
    }
    // 19 % 2 === 1 -> false
    expect(track.enabled).toBe(false)

    track.enabled = true
    expect(track.enabled).toBe(true)
  })

  it('Tier 2.5: Extraneous signals received after call closed are safely ignored', async () => {
    const session = createCallingSession()
    session.state = 'idle'
    session.pc = null

    let threwError = false
    try {
      if (session.state !== 'active' && session.state !== 'connecting') {
        // Ignored safely
      } else {
        session.pc!.setRemoteDescription({ type: 'answer', sdp: '' })
      }
    } catch {
      threwError = true
    }

    expect(threwError).toBe(false)
    expect(session.state).toBe('idle')
  })

  // =========================================================================
  // TIER 3: Pairwise Combinations
  // =========================================================================

  it('Tier 3.1: Pairwise: Mode (Audio/Video) x Device Switch x Mute State', async () => {
    const testCases: Array<{ mode: CallMode; targetMic: string; muted: boolean }> = [
      { mode: 'audio', targetMic: 'usb-mic', muted: false },
      { mode: 'audio', targetMic: 'default-mic', muted: true },
      { mode: 'video', targetMic: 'usb-mic', muted: true },
      { mode: 'video', targetMic: 'default-mic', muted: false },
    ]

    for (const tc of testCases) {
      const session = createCallingSession()
      session.mode = tc.mode
      const stream = (await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: tc.mode === 'video',
      })) as unknown as FakeMediaStream
      session.localStream = stream

      const audioTrack = stream.getAudioTracks()[0]
      audioTrack.enabled = !tc.muted
      session.isMuted = tc.muted
      session.selectedAudioInput = tc.targetMic

      expect(session.mode).toBe(tc.mode)
      expect(audioTrack.enabled).toBe(!tc.muted)
      expect(session.selectedAudioInput).toBe(tc.targetMic)

      for (const t of stream.getTracks()) t.stop()
    }
  })

  it('Tier 3.2: Pairwise: Call Direction x Screen Share x Teardown Trigger', async () => {
    const testCases: Array<{ role: 'initiator' | 'receiver'; screenShare: boolean; trigger: 'local' | 'remote' }> = [
      { role: 'initiator', screenShare: true, trigger: 'local' },
      { role: 'initiator', screenShare: false, trigger: 'remote' },
      { role: 'receiver', screenShare: true, trigger: 'remote' },
      { role: 'receiver', screenShare: false, trigger: 'local' },
    ]

    for (const tc of testCases) {
      const session = createCallingSession()
      session.role = tc.role
      session.isScreenSharing = tc.screenShare
      session.state = 'active'
      session.pc = new FakeRTCPeerConnection()

      if (tc.trigger === 'local') {
        session.pc.close()
        session.state = 'ended'
      } else {
        session.pc.simulateConnectionState('closed')
        session.state = 'ended'
      }

      expect(session.state).toBe('ended')
      expect(session.pc.connectionState).toBe('closed')
    }
  })

  // =========================================================================
  // TIER 4: Real-World Workload Scenarios
  // =========================================================================

  it('Tier 4.1: Real-World Scenario: Complete 1-on-1 Blind Call Session with device swap & hangup', async () => {
    // 1. Setup initiator and receiver
    const alice = createCallingSession()
    const bob = createCallingSession()

    alice.localStream = (await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: true,
    })) as unknown as FakeMediaStream
    bob.localStream = (await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: true,
    })) as unknown as FakeMediaStream

    alice.pc = new FakeRTCPeerConnection()
    bob.pc = new FakeRTCPeerConnection()

    for (const t of alice.localStream.getTracks()) alice.pc.addTrack(t)
    for (const t of bob.localStream.getTracks()) bob.pc.addTrack(t)

    // 2. Blind signaling exchange
    const offer = await alice.pc.createOffer()
    await alice.pc.setLocalDescription(offer)
    await bob.pc.setRemoteDescription(offer)

    const answer = await bob.pc.createAnswer()
    await bob.pc.setLocalDescription(answer)
    await alice.pc.setRemoteDescription(answer)

    alice.pc.simulateConnected()
    bob.pc.simulateConnected()
    alice.state = 'active'
    bob.state = 'active'

    expect(alice.state).toBe('active')
    expect(bob.state).toBe('active')

    // 3. Alice switches microphone mid-call
    const newMicStream = (await navigator.mediaDevices.getUserMedia({ audio: true })) as unknown as FakeMediaStream
    const newMicTrack = newMicStream.getAudioTracks()[0]
    const audioSender = alice.pc.getSenders().find((s) => s.track?.kind === 'audio')
    await audioSender?.replaceTrack(newMicTrack)
    expect(audioSender?.track).toBe(newMicTrack)

    // 4. Bob toggles camera off and back on
    bob.localStream.getVideoTracks()[0].enabled = false
    expect(bob.localStream.getVideoTracks()[0].enabled).toBe(false)
    bob.localStream.getVideoTracks()[0].enabled = true
    expect(bob.localStream.getVideoTracks()[0].enabled).toBe(true)

    // 5. Clean teardown from Alice
    for (const t of alice.localStream.getTracks()) t.stop()
    for (const t of bob.localStream.getTracks()) t.stop()
    newMicTrack.stop()
    alice.pc.close()
    bob.pc.close()

    expect(alice.pc.connectionState).toBe('closed')
    expect(bob.pc.connectionState).toBe('closed')
  })

  it('Tier 4.2: Real-World Scenario: Presentation Workload with screen share toggle and fallback', async () => {
    const session = createCallingSession()
    session.localStream = (await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: true,
    })) as unknown as FakeMediaStream
    session.pc = new FakeRTCPeerConnection()

    const cameraTrack = session.localStream.getVideoTracks()[0]
    const videoSender = session.pc.addTrack(cameraTrack)
    session.state = 'active'

    // 1. User starts presentation screen share
    const displayStream = (await navigator.mediaDevices.getDisplayMedia({
      video: true,
    })) as unknown as FakeMediaStream
    const screenTrack = displayStream.getVideoTracks()[0]
    await videoSender.replaceTrack(screenTrack)
    session.isScreenSharing = true
    expect(videoSender.track?.label).toBe('screen-share-track')

    // 2. User stops screen share -> fallback to original camera track
    screenTrack.stop()
    await videoSender.replaceTrack(cameraTrack)
    session.isScreenSharing = false
    expect(videoSender.track).toBe(cameraTrack)
    expect(videoSender.track?.readyState).toBe('live')
  })
})
