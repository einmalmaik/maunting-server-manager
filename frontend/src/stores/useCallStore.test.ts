/**
 * Anrufzustand gegen einen gefälschten LiveKit-Raum.
 *
 * Gemockt ist die Kapsel `services/livekitRaum`, nicht `livekit-client` selbst:
 * genau dort endet der Code, den dieses Projekt schreibt. `RoomEvent` und
 * `Track` kommen echt aus dem SDK, damit die Ereignisnamen nicht auseinander
 * laufen können.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RoomEvent, Track } from 'livekit-client'

// ── Gefälschter Raum ────────────────────────────────────────────────────────

type Handler = (...args: unknown[]) => void

class FakeParticipant {
  identity: string
  name: string
  private spuren = new Map<string, { isMuted: boolean; track: unknown }>()

  constructor(identity: string, name = '') {
    this.identity = identity
    this.name = name
  }

  getTrackPublication(quelle: string) {
    return this.spuren.get(quelle)
  }

  veroeffentliche(quelle: string, isMuted = false) {
    this.spuren.set(quelle, { isMuted, track: { quelle, sid: `${this.identity}-${quelle}` } })
  }
}

class FakeRoom {
  localParticipant = new FakeParticipant('u1', 'Ich')
  remoteParticipants = new Map<string, FakeParticipant>()
  private handler = new Map<string, Handler[]>()

  on(ereignis: string, fn: Handler) {
    const liste = this.handler.get(ereignis) ?? []
    liste.push(fn)
    this.handler.set(ereignis, liste)
    return this
  }

  /** Feuert ein Ereignis so, wie LiveKit es feuern würde. */
  feuere(ereignis: string, ...args: unknown[]) {
    for (const fn of this.handler.get(ereignis) ?? []) fn(...args)
  }

  tritt_bei(identity: string, name = '') {
    const teilnehmer = new FakeParticipant(identity, name)
    this.remoteParticipants.set(identity, teilnehmer)
    this.feuere(RoomEvent.ParticipantConnected, teilnehmer)
    return teilnehmer
  }

  verlaesst(identity: string) {
    const teilnehmer = this.remoteParticipants.get(identity)
    this.remoteParticipants.delete(identity)
    this.feuere(RoomEvent.ParticipantDisconnected, teilnehmer)
  }
}

let aktuellerRaum: FakeRoom

const livekit = {
  setzeMikrofon: vi.fn().mockResolvedValue(undefined),
  setzeKamera: vi.fn().mockResolvedValue(undefined),
  setzeTaub: vi.fn(),
  setzeLautstaerke: vi.fn(),
  wechsleGeraet: vi.fn().mockResolvedValue(undefined),
  starteBildschirmfreigabe: vi.fn().mockResolvedValue(undefined),
  beendeBildschirmfreigabe: vi.fn().mockResolvedValue(undefined),
  trenne: vi.fn().mockResolvedValue(undefined),
  erlaubeWiedergabe: vi.fn().mockResolvedValue(undefined),
  setzeRaumSchluessel: vi.fn().mockResolvedValue(undefined),
  verbinde: vi.fn(),
}

class E2eeNichtUnterstuetzt extends Error {}

vi.mock('@/services/livekitRaum', async () => {
  const echt = await vi.importActual<typeof import('livekit-client')>('livekit-client')
  return {
    RoomEvent: echt.RoomEvent,
    Track: echt.Track,
    FREIGABE_STANDARD: { aufloesung: '1080p', bildrate: 60, systemton: true },
    E2eeNichtUnterstuetzt,
    benutzerIdAusIdentity: (identity: string) =>
      /^u\d+$/.test(identity) ? Number(identity.slice(1)) : null,
    ...livekit,
  }
})

const api = {
  ladeZuAnrufEin: vi.fn(),
  holeInAnruf: vi.fn().mockResolvedValue({}),
  lehneAnrufAb: vi.fn().mockResolvedValue(undefined),
  brichAnrufAb: vi.fn().mockResolvedValue(undefined),
  holeZugang: vi.fn(),
  beendeGruppenanruf: vi.fn().mockResolvedValue(undefined),
}
vi.mock('@/api/calls', () => api)

const schluessel = {
  verteileAn: vi.fn().mockResolvedValue(undefined),
  verteileAnAlle: vi.fn().mockResolvedValue(undefined),
  entpacke: vi.fn(),
}
vi.mock('@/services/raumSchluessel', () => ({
  erzeugeRaumSchluessel: () => new Uint8Array(32).fill(7),
  alsArrayBuffer: (b: Uint8Array) => b.buffer,
  istSchluesselhalter: (eigene: number, anwesende: number[]) =>
    eigene === Math.min(...anwesende),
  ...schluessel,
}))

const toastFehler = vi.fn()
vi.mock('@/stores/toastStore', () => ({
  toast: { error: (t: string) => toastFehler(t), success: vi.fn(), info: vi.fn() },
}))

// Nach den Mocks importieren, sonst greifen sie nicht.
const { useCallStore, setzeAnrufIdentitaet } = await import('./useCallStore')

const FRISCH = useCallStore.getState()

const PARTNER = { userId: 2, username: 'bob', avatarUrl: null }

async function verbundenerAnruf() {
  await useCallStore.getState().initiateCall(PARTNER, 'audio')
  aktuellerRaum.feuere(RoomEvent.Connected)
}

beforeEach(() => {
  aktuellerRaum = new FakeRoom()
  vi.clearAllMocks()
  livekit.verbinde.mockImplementation(async () => ({ room: aktuellerRaum }))
  livekit.setzeMikrofon.mockResolvedValue(undefined)
  livekit.setzeKamera.mockResolvedValue(undefined)
  api.ladeZuAnrufEin.mockResolvedValue({
    signaling_token: 'raum-1',
    recipient_id: 2,
    expires_in: 120,
  })
  api.holeZugang.mockResolvedValue({
    url: 'wss://panel.test/livekit',
    token: 'jwt',
    raum: 'raum-1',
    identity: 'u1',
    ttl: 3600,
  })
  setzeAnrufIdentitaet({ userId: 1, publicKeyJwk: '{"kty":"RSA"}', decryptionKeys: ['k'] })
  useCallStore.setState(FRISCH, true)
})

afterEach(() => {
  useCallStore.setState(FRISCH, true)
  setzeAnrufIdentitaet(null)
})

// ── Anruf aufbauen ──────────────────────────────────────────────────────────

describe('Anruf aufbauen', () => {
  it('klingelt heraus und verbindet mit dem ausgegebenen Raum', async () => {
    await useCallStore.getState().initiateCall(PARTNER, 'audio')

    expect(api.ladeZuAnrufEin).toHaveBeenCalledWith(2, 'audio')
    expect(api.holeZugang).toHaveBeenCalledWith('direkt', 'raum-1', undefined)
    expect(livekit.verbinde).toHaveBeenCalledWith(
      'wss://panel.test/livekit',
      'jwt',
      expect.anything(),
    )
    expect(useCallStore.getState().raum).toBe('raum-1')
    expect(useCallStore.getState().partner).toEqual(PARTNER)
  })

  it('schickt den Raumschlüssel schon beim Klingeln', async () => {
    // Sonst wären die ersten Sekunden nach dem Abnehmen stumm.
    await useCallStore.getState().initiateCall(PARTNER, 'audio')
    expect(schluessel.verteileAn).toHaveBeenCalledWith(
      'raum-1',
      expect.anything(),
      2,
      '{"kty":"RSA"}',
    )
  })

  it('läuft sofort bei Connected auf aktiv, nicht erst beim ersten fremden Ton', async () => {
    await useCallStore.getState().initiateCall(PARTNER, 'audio')
    aktuellerRaum.feuere(RoomEvent.Connected)

    expect(useCallStore.getState().state).toBe('active')
    expect(useCallStore.getState().reconnecting).toBe(false)
  })

  it('schaltet bei einem Videoanruf die Kamera gleich frei', async () => {
    await useCallStore.getState().initiateCall(PARTNER, 'video')
    expect(livekit.setzeKamera).toHaveBeenCalledWith(aktuellerRaum, true)
    expect(useCallStore.getState().isCameraOff).toBe(false)
  })

  it('bleibt verbunden, wenn das Mikrofon klemmt, und sagt warum', async () => {
    const fehler = Object.assign(new Error('nope'), { name: 'NotAllowedError' })
    livekit.setzeMikrofon.mockRejectedValueOnce(fehler)

    await useCallStore.getState().initiateCall(PARTNER, 'audio')

    expect(useCallStore.getState().state).toBe('active')
    expect(useCallStore.getState().isMuted).toBe(true)
    expect(useCallStore.getState().errorMessage).toMatch(/blockiert/i)
  })

  it('meldet fehlende E2EE-Unterstützung im Klartext statt still zu scheitern', async () => {
    livekit.verbinde.mockRejectedValueOnce(
      new E2eeNichtUnterstuetzt('Dieser Browser kann keine verschlüsselten Anrufe.'),
    )

    await useCallStore.getState().initiateCall(PARTNER, 'audio')

    expect(toastFehler).toHaveBeenCalledWith('Dieser Browser kann keine verschlüsselten Anrufe.')
    expect(useCallStore.getState().state).toBe('idle')
  })

  it('nimmt einen eingehenden Anruf erst auf Klick an', async () => {
    useCallStore.getState().receiveCall(PARTNER, 'audio', 'raum-9')
    expect(useCallStore.getState().state).toBe('incoming')
    expect(livekit.verbinde).not.toHaveBeenCalled()

    api.holeZugang.mockResolvedValueOnce({
      url: 'wss://panel.test/livekit',
      token: 'jwt',
      raum: 'raum-9',
      identity: 'u1',
      ttl: 3600,
    })
    await useCallStore.getState().acceptCall()

    expect(api.holeZugang).toHaveBeenCalledWith('direkt', 'raum-9', undefined)
    expect(useCallStore.getState().state).toBe('active')
  })

  it('bricht beim Auflegen im Klingelzustand die Einladung serverseitig ab', async () => {
    api.holeZugang.mockImplementation(() => new Promise(() => {}))
    void useCallStore.getState().initiateCall(PARTNER, 'audio')
    await vi.waitFor(() => expect(useCallStore.getState().state).toBe('outgoing'))

    useCallStore.getState().endCall()
    expect(api.brichAnrufAb).toHaveBeenCalledWith('raum-1')
  })

  it('lehnt einen eingehenden Anruf serverseitig ab', () => {
    useCallStore.getState().receiveCall(PARTNER, 'audio', 'raum-9')
    useCallStore.getState().rejectCall()

    expect(api.lehneAnrufAb).toHaveBeenCalledWith('raum-9')
    expect(useCallStore.getState().state).toBe('idle')
  })
})

// ── Teilnehmer ──────────────────────────────────────────────────────────────

describe('Teilnehmer', () => {
  it('führt sich selbst und alle anderen im Raum', async () => {
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u2', 'bob')

    const ids = useCallStore.getState().participants.map((t) => t.userId)
    expect(ids).toContain(1)
    expect(ids).toContain(2)
    expect(useCallStore.getState().participants.find((t) => t.userId === 1)?.isSelf).toBe(true)
  })

  it('markiert, wer gerade spricht', async () => {
    await verbundenerAnruf()
    const bob = aktuellerRaum.tritt_bei('u2', 'bob')

    aktuellerRaum.feuere(RoomEvent.ActiveSpeakersChanged, [bob])
    expect(
      useCallStore.getState().participants.find((t) => t.identity === 'u2')?.isSpeaking,
    ).toBe(true)

    aktuellerRaum.feuere(RoomEvent.ActiveSpeakersChanged, [])
    expect(
      useCallStore.getState().participants.find((t) => t.identity === 'u2')?.isSpeaking,
    ).toBe(false)
  })

  it('zeigt einen stummen Teilnehmer als stumm', async () => {
    await verbundenerAnruf()
    const bob = aktuellerRaum.tritt_bei('u2', 'bob')
    bob.veroeffentliche(Track.Source.Microphone, true)
    aktuellerRaum.feuere(RoomEvent.TrackMuted)

    expect(useCallStore.getState().participants.find((t) => t.identity === 'u2')?.isMuted).toBe(
      true,
    )
  })

  it('schickt einem Nachzügler den Raumschlüssel, wenn man selbst der Halter ist', async () => {
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u5')

    // Eigene Kennung 1 ist die kleinste im Raum, also verteilt dieser Client.
    await vi.waitFor(() =>
      expect(schluessel.verteileAn).toHaveBeenCalledWith('raum-1', expect.anything(), 5, expect.any(String)),
    )
  })

  it('beendet ein Zweiergespräch, wenn das Gegenüber geht', async () => {
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u2', 'bob')
    aktuellerRaum.verlaesst('u2')

    expect(useCallStore.getState().state).toBe('idle')
  })
})

// ── Steuerung ───────────────────────────────────────────────────────────────

describe('Mikrofon und Taubheit', () => {
  it('schaltet das Mikrofon stumm und wieder frei', async () => {
    await verbundenerAnruf()

    useCallStore.getState().toggleMute()
    expect(useCallStore.getState().isMuted).toBe(true)
    expect(livekit.setzeMikrofon).toHaveBeenLastCalledWith(aktuellerRaum, false)

    useCallStore.getState().toggleMute()
    expect(useCallStore.getState().isMuted).toBe(false)
    expect(livekit.setzeMikrofon).toHaveBeenLastCalledWith(aktuellerRaum, true)
  })

  it('macht Taubheit beidseitig: nichts hören und nichts senden', async () => {
    await verbundenerAnruf()

    useCallStore.getState().toggleDeafen()

    expect(useCallStore.getState().isDeafened).toBe(true)
    expect(useCallStore.getState().isMuted).toBe(true)
    expect(livekit.setzeTaub).toHaveBeenCalledWith(aktuellerRaum, true)
    expect(livekit.setzeMikrofon).toHaveBeenLastCalledWith(aktuellerRaum, false)
  })

  it('hebt die Taubheit auf, wenn das Mikrofon wieder freigeschaltet wird', async () => {
    await verbundenerAnruf()
    useCallStore.getState().toggleDeafen()

    useCallStore.getState().toggleMute()

    // Sonst spräche man in einen Raum, den man selbst nicht hört.
    expect(useCallStore.getState().isDeafened).toBe(false)
    expect(useCallStore.getState().isMuted).toBe(false)
    expect(livekit.setzeTaub).toHaveBeenLastCalledWith(aktuellerRaum, false)
  })

  it('schaltet die Kamera mitten im Gespräch an und wieder aus', async () => {
    await verbundenerAnruf()

    useCallStore.getState().toggleCamera()
    await vi.waitFor(() => expect(useCallStore.getState().isCameraOff).toBe(false))
    expect(livekit.setzeKamera).toHaveBeenLastCalledWith(aktuellerRaum, true)

    useCallStore.getState().toggleCamera()
    await vi.waitFor(() => expect(useCallStore.getState().isCameraOff).toBe(true))
    expect(livekit.setzeKamera).toHaveBeenLastCalledWith(aktuellerRaum, false)
  })
})

describe('Bildschirmfreigabe', () => {
  it('reicht die gewählten Einstellungen durch und merkt sie sich', async () => {
    await verbundenerAnruf()
    const optionen = { aufloesung: '1440p' as const, bildrate: 60 as const, systemton: true }

    await useCallStore.getState().startScreenShare(optionen)

    expect(livekit.starteBildschirmfreigabe).toHaveBeenCalledWith(aktuellerRaum, optionen)
    expect(useCallStore.getState().isScreenSharing).toBe(true)
    expect(useCallStore.getState().screenShareOptions).toEqual(optionen)
  })

  it('schweigt, wenn im Auswahldialog abgebrochen wird', async () => {
    await verbundenerAnruf()
    livekit.starteBildschirmfreigabe.mockRejectedValueOnce(
      Object.assign(new Error('abgebrochen'), { name: 'NotAllowedError' }),
    )

    await useCallStore.getState().startScreenShare(useCallStore.getState().screenShareOptions)

    expect(toastFehler).not.toHaveBeenCalled()
    expect(useCallStore.getState().isScreenSharing).toBe(false)
  })

  it('führt fremde Freigaben als eigene Bühne', async () => {
    await verbundenerAnruf()
    const bob = aktuellerRaum.tritt_bei('u2', 'bob')
    bob.veroeffentliche(Track.Source.ScreenShare)
    aktuellerRaum.feuere(RoomEvent.TrackSubscribed)

    const freigaben = useCallStore.getState().screenShares
    expect(freigaben).toHaveLength(1)
    expect(freigaben[0]).toMatchObject({ identity: 'u2', userId: 2, ownerName: 'bob' })

    useCallStore.getState().setFocusedShare('u2')
    expect(useCallStore.getState().focusedShareIdentity).toBe('u2')
  })
})

describe('Jemanden nachholen', () => {
  it('lädt ein und reicht den Raumschlüssel gleich mit', async () => {
    await verbundenerAnruf()

    await useCallStore.getState().inviteToCall(4)

    expect(api.holeInAnruf).toHaveBeenCalledWith('raum-1', 4, 'audio')
    expect(schluessel.verteileAn).toHaveBeenCalledWith('raum-1', expect.anything(), 4, expect.any(String))
  })

  it('gilt nur für Zweiergespräche, nicht für Gruppenräume', async () => {
    await useCallStore.getState().joinGroupCall(
      { id: 3, name: 'Team', avatarUrl: null, canShare: true, canModerate: true },
      'grp_abc',
    )

    await useCallStore.getState().inviteToCall(4)
    expect(api.holeInAnruf).not.toHaveBeenCalled()
  })
})

// ── Gruppenanruf ────────────────────────────────────────────────────────────

describe('Gruppenanruf', () => {
  const GRUPPE = { id: 3, name: 'Team', avatarUrl: null, canShare: true, canModerate: true }

  it('holt ein Token für den Gruppenraum und verteilt den Schlüssel an alle', async () => {
    await useCallStore.getState().joinGroupCall(GRUPPE, 'grp_abc', [1, 2, 5])

    expect(api.holeZugang).toHaveBeenCalledWith('gruppe', 'grp_abc', 3)
    // An alle ausser sich selbst.
    expect(schluessel.verteileAnAlle).toHaveBeenCalledWith(
      'grp_abc',
      expect.anything(),
      [2, 5],
      expect.any(String),
    )
    expect(useCallStore.getState().kind).toBe('gruppe')
    expect(useCallStore.getState().group).toEqual(GRUPPE)
  })

  it('verteilt beim Beitreten zu einem laufenden Anruf nichts', async () => {
    // Der Schlüssel liegt dort schon; ein neuer würde alle aussperren.
    await useCallStore.getState().joinGroupCall(GRUPPE, 'grp_abc')
    expect(schluessel.verteileAnAlle).not.toHaveBeenCalled()
  })

  it('schliesst den Raum nur, wenn man ihn moderieren darf', async () => {
    await useCallStore.getState().joinGroupCall(GRUPPE, 'grp_abc')
    useCallStore.getState().endCall()
    expect(api.beendeGruppenanruf).toHaveBeenCalledWith(3, 'grp_abc')

    vi.clearAllMocks()
    await useCallStore
      .getState()
      .joinGroupCall({ ...GRUPPE, canModerate: false }, 'grp_xyz')
    useCallStore.getState().endCall()
    expect(api.beendeGruppenanruf).not.toHaveBeenCalled()
  })

  it('hält den Raum offen, wenn ein einzelner Teilnehmer geht', async () => {
    await useCallStore.getState().joinGroupCall(GRUPPE, 'grp_abc')
    aktuellerRaum.feuere(RoomEvent.Connected)
    aktuellerRaum.tritt_bei('u2')
    aktuellerRaum.verlaesst('u2')

    expect(useCallStore.getState().state).toBe('active')
  })
})

// ── Raumschlüssel ───────────────────────────────────────────────────────────

describe('Raumschlüssel', () => {
  it('übernimmt einen Umschlag für den eigenen Raum', async () => {
    await verbundenerAnruf()
    schluessel.entpacke.mockResolvedValueOnce(new Uint8Array(32).fill(9))

    await useCallStore.getState().acceptRoomKey('raum-1', 'sv-e2ee-hybrid-v1.xyz')

    expect(schluessel.entpacke).toHaveBeenCalledWith('sv-e2ee-hybrid-v1.xyz', ['k'])
    expect(livekit.setzeRaumSchluessel).toHaveBeenCalled()
  })

  it('ignoriert einen Umschlag für einen fremden Raum', async () => {
    await verbundenerAnruf()

    await useCallStore.getState().acceptRoomKey('ein-anderer-raum', 'sv-e2ee-hybrid-v1.xyz')

    expect(schluessel.entpacke).not.toHaveBeenCalled()
    expect(livekit.setzeRaumSchluessel).not.toHaveBeenCalled()
  })

  it('läuft weiter, wenn ein Umschlag nicht zu öffnen ist', async () => {
    await verbundenerAnruf()
    schluessel.entpacke.mockRejectedValueOnce(new Error('nicht für uns'))

    await expect(
      useCallStore.getState().acceptRoomKey('raum-1', 'kaputt'),
    ).resolves.toBeUndefined()
    expect(useCallStore.getState().state).toBe('active')
  })
})

// ── Aufräumen ───────────────────────────────────────────────────────────────

describe('Auflegen', () => {
  it('trennt den Raum und setzt alles zurück', async () => {
    await verbundenerAnruf()
    useCallStore.getState().toggleDeafen()

    useCallStore.getState().endCall()

    expect(livekit.trenne).toHaveBeenCalledWith(aktuellerRaum)
    const zustand = useCallStore.getState()
    expect(zustand.state).toBe('idle')
    expect(zustand.raum).toBeNull()
    expect(zustand.partner).toBeNull()
    expect(zustand.participants).toEqual([])
    expect(zustand.screenShares).toEqual([])
    expect(zustand.isDeafened).toBe(false)
    expect(zustand.isMuted).toBe(false)
    expect(zustand.callDurationSeconds).toBe(0)
  })

  it('lässt Ereignisse eines alten Anrufs verpuffen', async () => {
    await verbundenerAnruf()
    const alterRaum = aktuellerRaum
    useCallStore.getState().endCall()

    // Ein später Rückläufer aus dem beendeten Anruf darf den Zustand nicht
    // wieder auf „aktiv" ziehen.
    alterRaum.feuere(RoomEvent.Connected)
    expect(useCallStore.getState().state).toBe('idle')
  })

  it('zählt die Gesprächsdauer erst ab dem Verbinden', async () => {
    await verbundenerAnruf()
    expect(useCallStore.getState().callDurationSeconds).toBe(0)

    useCallStore.getState().incrementDuration()
    expect(useCallStore.getState().callDurationSeconds).toBe(1)
  })
})
