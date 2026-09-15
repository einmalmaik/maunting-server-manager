/**
 * Deterministic WebRTC & MediaStream Test Mocks for Vitest / JSDOM.
 *
 * Implements:
 * - FakeRTCPeerConnection with encoded insertable streams (ReadableStream / WritableStream)
 * - FakeRTCRtpSender / FakeRTCRtpReceiver with createEncodedStreams()
 * - FakeMediaStreamTrack and FakeMediaStream
 * - FakeMediaRecorder with start/stop/dataavailable lifecycle
 * - installFakeWebRtc() global test installer and restore mechanism
 *
 * Patterned after fakeAudio.ts and fakeWebSocket.ts.
 */

export interface FakeRTCEncodedFrame {
  data: ArrayBuffer
  timestamp: number
  type?: 'key' | 'delta' | 'empty'
  getMetadata(): {
    ssrc?: number
    payloadType?: number
    synchronizationSource?: number
    csrcs?: number[]
    contributingSources?: number[]
    mimeType?: string
  }
}

export class FakeMediaStreamTrack {
  id: string
  kind: 'audio' | 'video'
  label: string
  enabled = true
  muted = false
  readyState: 'live' | 'ended' = 'live'
  onended: (() => void) | null = null
  onmute: (() => void) | null = null
  onunmute: (() => void) | null = null
  private listeners: Map<string, Set<EventListenerOrEventListenerObject>> = new Map()

  constructor(kind: 'audio' | 'video' = 'audio', label = `mock-${kind}-track`) {
    this.id = `track-${Math.random().toString(36).substring(2, 9)}`
    this.kind = kind
    this.label = label
  }

  stop(): void {
    if (this.readyState === 'ended') return
    this.readyState = 'ended'
    this.onended?.()
    this.dispatchEvent(new Event('ended'))
  }

  clone(): FakeMediaStreamTrack {
    const cloned = new FakeMediaStreamTrack(this.kind, this.label)
    cloned.enabled = this.enabled
    cloned.muted = this.muted
    return cloned
  }

  applyConstraints(_constraints?: MediaTrackConstraints): Promise<void> {
    return Promise.resolve()
  }

  getSettings(): MediaTrackSettings {
    return {
      deviceId: this.id,
      facingMode: this.kind === 'video' ? 'user' : undefined,
    }
  }

  getConstraints(): MediaTrackConstraints {
    return {}
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set())
    }
    this.listeners.get(type)!.add(listener)
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    this.listeners.get(type)?.delete(listener)
  }

  dispatchEvent(event: Event): boolean {
    const set = this.listeners.get(event.type)
    if (set) {
      set.forEach((listener) => {
        if (typeof listener === 'function') {
          listener(event)
        } else if ('handleEvent' in listener) {
          listener.handleEvent(event)
        }
      })
    }
    return true
  }
}

export class FakeMediaStream {
  id: string
  active = true
  private tracks: FakeMediaStreamTrack[] = []

  constructor(tracks?: FakeMediaStreamTrack[]) {
    this.id = `stream-${Math.random().toString(36).substring(2, 9)}`
    if (tracks) {
      this.tracks = [...tracks]
    }
  }

  getTracks(): FakeMediaStreamTrack[] {
    return [...this.tracks]
  }

  getAudioTracks(): FakeMediaStreamTrack[] {
    return this.tracks.filter((t) => t.kind === 'audio')
  }

  getVideoTracks(): FakeMediaStreamTrack[] {
    return this.tracks.filter((t) => t.kind === 'video')
  }

  addTrack(track: FakeMediaStreamTrack): void {
    if (!this.tracks.includes(track)) {
      this.tracks.push(track)
    }
  }

  removeTrack(track: FakeMediaStreamTrack): void {
    const idx = this.tracks.indexOf(track)
    if (idx !== -1) {
      this.tracks.splice(idx, 1)
    }
  }

  getTrackById(id: string): FakeMediaStreamTrack | undefined {
    return this.tracks.find((t) => t.id === id)
  }

  clone(): FakeMediaStream {
    return new FakeMediaStream(this.tracks.map((t) => t.clone()))
  }
}

export class FakeRTCRtpSender {
  track: FakeMediaStreamTrack | null
  private readableController: ReadableStreamDefaultController<FakeRTCEncodedFrame> | null = null
  private encodedReadable: ReadableStream<FakeRTCEncodedFrame>
  private encodedWritable: WritableStream<FakeRTCEncodedFrame>
  readonly writtenFrames: FakeRTCEncodedFrame[] = []

  constructor(track: FakeMediaStreamTrack | null) {
    this.track = track
    this.encodedReadable = new ReadableStream<FakeRTCEncodedFrame>({
      start: (controller) => {
        this.readableController = controller
      },
    })
    this.encodedWritable = new WritableStream<FakeRTCEncodedFrame>({
      write: (frame) => {
        this.writtenFrames.push(frame)
      },
    })
  }

  createEncodedStreams(): {
    readable: ReadableStream<FakeRTCEncodedFrame>
    writable: WritableStream<FakeRTCEncodedFrame>
  } {
    return {
      readable: this.encodedReadable,
      writable: this.encodedWritable,
    }
  }

  replaceTrack(newTrack: FakeMediaStreamTrack | null): Promise<void> {
    this.track = newTrack
    return Promise.resolve()
  }

  getParameters(): RTCRtpSendParameters {
    return {
      codecs: [],
      headerExtensions: [],
      encodings: [],
      rtcp: { cname: '', reducedSize: false },
      transactionId: '',
    }
  }

  setParameters(): Promise<void> {
    return Promise.resolve()
  }

  // Test helper
  pushFrame(frame: FakeRTCEncodedFrame): void {
    this.readableController?.enqueue(frame)
  }
}

export class FakeRTCRtpReceiver {
  track: FakeMediaStreamTrack
  private readableController: ReadableStreamDefaultController<FakeRTCEncodedFrame> | null = null
  private encodedReadable: ReadableStream<FakeRTCEncodedFrame>
  private encodedWritable: WritableStream<FakeRTCEncodedFrame>
  readonly deliveredFrames: FakeRTCEncodedFrame[] = []

  constructor(track: FakeMediaStreamTrack) {
    this.track = track
    this.encodedReadable = new ReadableStream<FakeRTCEncodedFrame>({
      start: (controller) => {
        this.readableController = controller
      },
    })
    this.encodedWritable = new WritableStream<FakeRTCEncodedFrame>({
      write: (frame) => {
        this.deliveredFrames.push(frame)
      },
    })
  }

  createEncodedStreams(): {
    readable: ReadableStream<FakeRTCEncodedFrame>
    writable: WritableStream<FakeRTCEncodedFrame>
  } {
    return {
      readable: this.encodedReadable,
      writable: this.encodedWritable,
    }
  }

  getParameters(): RTCRtpReceiveParameters {
    return {
      codecs: [],
      headerExtensions: [],
      rtcp: { cname: '', reducedSize: false },
    }
  }

  // Test helper
  pushFrame(frame: FakeRTCEncodedFrame): void {
    this.readableController?.enqueue(frame)
  }
}

export class FakeRTCPeerConnection {
  static instances: FakeRTCPeerConnection[] = []

  localDescription: RTCSessionDescriptionInit | null = null
  remoteDescription: RTCSessionDescriptionInit | null = null
  connectionState: RTCPeerConnectionState = 'new'
  iceConnectionState: RTCIceConnectionState = 'new'
  signalingState: RTCSignalingState = 'stable'
  iceGatheringState: RTCIceGatheringState = 'new'

  onicecandidate: ((event: { candidate: RTCIceCandidate | null }) => void) | null = null
  ontrack: ((event: { track: FakeMediaStreamTrack; streams: FakeMediaStream[]; receiver: FakeRTCRtpReceiver }) => void) | null = null
  onconnectionstatechange: ((event: Event) => void) | null = null
  oniceconnectionstatechange: ((event: Event) => void) | null = null
  onsignalingstatechange: ((event: Event) => void) | null = null

  private senders: FakeRTCRtpSender[] = []
  private receivers: FakeRTCRtpReceiver[] = []
  private listeners: Map<string, Set<EventListenerOrEventListenerObject>> = new Map()

  private defaultSenderReadableController: ReadableStreamDefaultController<FakeRTCEncodedFrame> | null = null
  readonly defaultEncodedReadable: ReadableStream<FakeRTCEncodedFrame>
  readonly defaultEncodedWritable: WritableStream<FakeRTCEncodedFrame>
  readonly defaultWrittenFrames: FakeRTCEncodedFrame[] = []

  readonly candidatesSent: RTCIceCandidateInit[] = []
  readonly config: RTCConfiguration | undefined

  constructor(configuration?: RTCConfiguration) {
    this.config = configuration
    this.defaultEncodedReadable = new ReadableStream<FakeRTCEncodedFrame>({
      start: (controller) => {
        this.defaultSenderReadableController = controller
      },
    })
    this.defaultEncodedWritable = new WritableStream<FakeRTCEncodedFrame>({
      write: (frame) => {
        this.defaultWrittenFrames.push(frame)
      },
    })
    FakeRTCPeerConnection.instances.push(this)
  }

  createEncodedStreams(): {
    readable: ReadableStream<FakeRTCEncodedFrame>
    writable: WritableStream<FakeRTCEncodedFrame>
  } {
    return {
      readable: this.defaultEncodedReadable,
      writable: this.defaultEncodedWritable,
    }
  }

  createOffer(_options?: RTCOfferOptions): Promise<RTCSessionDescriptionInit> {
    const offer: RTCSessionDescriptionInit = {
      type: 'offer',
      sdp: `v=0\r\no=msm-alice 123456 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 00:11:22\r\n`,
    }
    return Promise.resolve(offer)
  }

  createAnswer(_options?: RTCAnswerOptions): Promise<RTCSessionDescriptionInit> {
    const answer: RTCSessionDescriptionInit = {
      type: 'answer',
      sdp: `v=0\r\no=msm-bob 654321 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 33:44:55\r\n`,
    }
    return Promise.resolve(answer)
  }

  setLocalDescription(desc: RTCSessionDescriptionInit): Promise<void> {
    this.localDescription = desc
    this.signalingState = desc.type === 'offer' ? 'have-local-offer' : 'stable'
    this.emitSignalingChange()
    return Promise.resolve()
  }

  setRemoteDescription(desc: RTCSessionDescriptionInit): Promise<void> {
    this.remoteDescription = desc
    this.signalingState = desc.type === 'offer' ? 'have-remote-offer' : 'stable'
    this.emitSignalingChange()
    return Promise.resolve()
  }

  addIceCandidate(candidate: RTCIceCandidateInit): Promise<void> {
    this.candidatesSent.push(candidate)
    return Promise.resolve()
  }

  addTrack(track: FakeMediaStreamTrack, ..._streams: FakeMediaStream[]): FakeRTCRtpSender {
    const sender = new FakeRTCRtpSender(track)
    this.senders.push(sender)
    return sender
  }

  removeTrack(sender: FakeRTCRtpSender): void {
    const idx = this.senders.indexOf(sender)
    if (idx !== -1) {
      this.senders.splice(idx, 1)
      sender.track = null
    }
  }

  getSenders(): FakeRTCRtpSender[] {
    return [...this.senders]
  }

  getReceivers(): FakeRTCRtpReceiver[] {
    return [...this.receivers]
  }

  close(): void {
    if (this.connectionState === 'closed') return
    this.connectionState = 'closed'
    this.iceConnectionState = 'closed'
    this.signalingState = 'closed'
    this.emitConnectionStateChange()
    this.emitIceConnectionStateChange()
    this.emitSignalingChange()
  }

  // --- Deterministic Test Helpers ---

  simulateConnectionState(state: RTCPeerConnectionState): void {
    this.connectionState = state
    this.emitConnectionStateChange()
  }

  simulateIceConnectionState(state: RTCIceConnectionState): void {
    this.iceConnectionState = state
    this.emitIceConnectionStateChange()
  }

  simulateConnected(): void {
    this.signalingState = 'stable'
    this.connectionState = 'connected'
    this.iceConnectionState = 'connected'
    this.emitSignalingChange()
    this.emitConnectionStateChange()
    this.emitIceConnectionStateChange()
  }

  simulateRemoteTrack(track: FakeMediaStreamTrack, stream?: FakeMediaStream): FakeRTCRtpReceiver {
    const receiver = new FakeRTCRtpReceiver(track)
    this.receivers.push(receiver)
    const streamObj = stream ?? new FakeMediaStream([track])
    const event = { track, streams: [streamObj], receiver }
    this.ontrack?.(event)
    this.dispatchEvent(new CustomEvent('track', { detail: event }))
    return receiver
  }

  simulateLocalIceCandidate(candidate: RTCIceCandidateInit | null): void {
    this.onicecandidate?.({ candidate: candidate as RTCIceCandidate | null })
    this.dispatchEvent(new CustomEvent('icecandidate', { detail: { candidate } }))
  }

  simulateIncomingFrame(frame: FakeRTCEncodedFrame): void {
    this.defaultSenderReadableController?.enqueue(frame)
  }

  private emitConnectionStateChange(): void {
    const ev = new Event('connectionstatechange')
    this.onconnectionstatechange?.(ev)
    this.dispatchEvent(ev)
  }

  private emitIceConnectionStateChange(): void {
    const ev = new Event('iceconnectionstatechange')
    this.oniceconnectionstatechange?.(ev)
    this.dispatchEvent(ev)
  }

  private emitSignalingChange(): void {
    const ev = new Event('signalingstatechange')
    this.onsignalingstatechange?.(ev)
    this.dispatchEvent(ev)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set())
    }
    this.listeners.get(type)!.add(listener)
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    this.listeners.get(type)?.delete(listener)
  }

  dispatchEvent(event: Event): boolean {
    const set = this.listeners.get(event.type)
    if (set) {
      set.forEach((listener) => {
        if (typeof listener === 'function') {
          listener(event)
        } else if ('handleEvent' in listener) {
          listener.handleEvent(event)
        }
      })
    }
    return true
  }
}

export class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = []
  static supportedTypes = new Set([
    'video/webm',
    'video/webm;codecs=vp8',
    'video/webm;codecs=vp9',
    'video/webm;codecs=h264',
    'video/mp4',
    'audio/webm',
    'audio/webm;codecs=opus',
    'audio/ogg',
  ])

  static isTypeSupported(mimeType: string): boolean {
    const base = mimeType.split(';')[0].trim().toLowerCase()
    return FakeMediaRecorder.supportedTypes.has(base) || FakeMediaRecorder.supportedTypes.has(mimeType.toLowerCase())
  }

  stream: FakeMediaStream
  mimeType: string
  state: 'inactive' | 'recording' | 'paused' = 'inactive'
  timeslice?: number

  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstart: (() => void) | null = null
  onstop: (() => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onpause: (() => void) | null = null
  onresume: (() => void) | null = null

  recordedChunks: Blob[] = []

  constructor(stream: FakeMediaStream, options?: { mimeType?: string }) {
    this.stream = stream
    this.mimeType = options?.mimeType ?? 'video/webm'
    FakeMediaRecorder.instances.push(this)
  }

  start(timeslice?: number): void {
    if (this.state !== 'inactive') {
      throw new Error(`Failed to execute 'start' on 'MediaRecorder': state is '${this.state}'`)
    }
    this.timeslice = timeslice
    this.state = 'recording'
    this.recordedChunks = []
    this.onstart?.()
  }

  stop(): void {
    if (this.state === 'inactive') {
      throw new Error("Failed to execute 'stop' on 'MediaRecorder': state is 'inactive'")
    }
    this.state = 'inactive'

    if (this.recordedChunks.length === 0) {
      const chunk = new Blob([new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0x01, 0x02, 0x03, 0x04])], {
        type: this.mimeType,
      })
      this.recordedChunks.push(chunk)
      this.ondataavailable?.({ data: chunk })
    }

    this.onstop?.()
  }

  pause(): void {
    if (this.state === 'recording') {
      this.state = 'paused'
      this.onpause?.()
    }
  }

  resume(): void {
    if (this.state === 'paused') {
      this.state = 'recording'
      this.onresume?.()
    }
  }

  requestData(): void {
    if (this.state === 'inactive') return
    const chunk = new Blob([new Uint8Array([0xaa, 0xbb, 0xcc])], { type: this.mimeType })
    this.recordedChunks.push(chunk)
    this.ondataavailable?.({ data: chunk })
  }

  simulateChunk(data: Blob | Uint8Array | string): void {
    const blob = data instanceof Blob ? data : new Blob([data as unknown as BlobPart], { type: this.mimeType })
    this.recordedChunks.push(blob)
    this.ondataavailable?.({ data: blob })
  }
}

export interface FakeWebRtcSetup {
  restore: () => void
  instances: FakeRTCPeerConnection[]
  recorders: FakeMediaRecorder[]
  getUserMediaCalls: Array<MediaStreamConstraints | undefined>
  getDisplayMediaCalls: Array<DisplayMediaStreamOptions | undefined>
  setDevices: (devices: MediaDeviceInfo[]) => void
}

export function installFakeWebRtc(options?: {
  denyPermission?: boolean
  devices?: MediaDeviceInfo[]
  noSpeakerSupport?: boolean
}): FakeWebRtcSetup {
  const originalRtc = (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection
  const originalWebKitRtc = (globalThis as { webkitRTCPeerConnection?: unknown }).webkitRTCPeerConnection
  const originalMediaRecorder = (globalThis as { MediaRecorder?: unknown }).MediaRecorder
  const originalMediaStream = (globalThis as { MediaStream?: unknown }).MediaStream
  const originalMediaStreamTrack = (globalThis as { MediaStreamTrack?: unknown }).MediaStreamTrack
  const originalMediaDevices = navigator.mediaDevices

  FakeRTCPeerConnection.instances = []
  FakeMediaRecorder.instances = []

  const getUserMediaCalls: Array<MediaStreamConstraints | undefined> = []
  const getDisplayMediaCalls: Array<DisplayMediaStreamOptions | undefined> = []

  let currentDevices: MediaDeviceInfo[] = options?.devices ?? [
    {
      deviceId: 'default-mic',
      groupId: 'group-1',
      kind: 'audioinput',
      label: 'Standard-Mikrofon (Integriert)',
      toJSON: () => ({}),
    } as MediaDeviceInfo,
    {
      deviceId: 'usb-mic',
      groupId: 'group-2',
      kind: 'audioinput',
      label: 'USB Studio Mikrofon',
      toJSON: () => ({}),
    } as MediaDeviceInfo,
    {
      deviceId: 'front-camera',
      groupId: 'group-1',
      kind: 'videoinput',
      label: 'Vorderkamera (FaceTime HD)',
      toJSON: () => ({}),
    } as MediaDeviceInfo,
    {
      deviceId: 'back-camera',
      groupId: 'group-3',
      kind: 'videoinput',
      label: 'Rückkamera',
      toJSON: () => ({}),
    } as MediaDeviceInfo,
    ...(options?.noSpeakerSupport
      ? []
      : [
          {
            deviceId: 'default-speaker',
            groupId: 'group-1',
            kind: 'audiooutput',
            label: 'Lautsprecher (Realtek Audio)',
            toJSON: () => ({}),
          } as MediaDeviceInfo,
          {
            deviceId: 'headset-speaker',
            groupId: 'group-2',
            kind: 'audiooutput',
            label: 'Kopfhörer (USB Wireless)',
            toJSON: () => ({}),
          } as MediaDeviceInfo,
        ]),
  ]

  ;(globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = FakeRTCPeerConnection
  ;(globalThis as { webkitRTCPeerConnection?: unknown }).webkitRTCPeerConnection = FakeRTCPeerConnection
  ;(globalThis as { MediaRecorder?: unknown }).MediaRecorder = FakeMediaRecorder
  ;(globalThis as { MediaStream?: unknown }).MediaStream = FakeMediaStream
  ;(globalThis as { MediaStreamTrack?: unknown }).MediaStreamTrack = FakeMediaStreamTrack

  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: (constraints?: MediaStreamConstraints) => {
        getUserMediaCalls.push(constraints)
        if (options?.denyPermission) {
          const err = new Error('Permission denied')
          err.name = 'NotAllowedError'
          return Promise.reject(err)
        }

        const tracks: FakeMediaStreamTrack[] = []
        if (constraints?.audio) {
          tracks.push(new FakeMediaStreamTrack('audio', 'mock-microphone-track'))
        }
        if (constraints?.video) {
          tracks.push(new FakeMediaStreamTrack('video', 'mock-camera-track'))
        }
        return Promise.resolve(new FakeMediaStream(tracks))
      },
      getDisplayMedia: (optionsMedia?: DisplayMediaStreamOptions) => {
        getDisplayMediaCalls.push(optionsMedia)
        if (options?.denyPermission) {
          const err = new Error('Screen share permission denied')
          err.name = 'NotAllowedError'
          return Promise.reject(err)
        }
        const videoTrack = new FakeMediaStreamTrack('video', 'screen-share-track')
        return Promise.resolve(new FakeMediaStream([videoTrack]))
      },
      enumerateDevices: () => {
        return Promise.resolve([...currentDevices])
      },
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => true,
    },
  })

  return {
    restore: () => {
      ;(globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = originalRtc
      ;(globalThis as { webkitRTCPeerConnection?: unknown }).webkitRTCPeerConnection = originalWebKitRtc
      ;(globalThis as { MediaRecorder?: unknown }).MediaRecorder = originalMediaRecorder
      ;(globalThis as { MediaStream?: unknown }).MediaStream = originalMediaStream
      ;(globalThis as { MediaStreamTrack?: unknown }).MediaStreamTrack = originalMediaStreamTrack
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: originalMediaDevices,
      })
      FakeRTCPeerConnection.instances = []
      FakeMediaRecorder.instances = []
    },
    instances: FakeRTCPeerConnection.instances,
    recorders: FakeMediaRecorder.instances,
    getUserMediaCalls,
    getDisplayMediaCalls,
    setDevices: (devices: MediaDeviceInfo[]) => {
      currentDevices = [...devices]
    },
  }
}
