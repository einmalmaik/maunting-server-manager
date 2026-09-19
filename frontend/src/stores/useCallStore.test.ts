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
import type { ActiveCallInfo } from '@/api/calls'

// ── Gefälschter Raum ────────────────────────────────────────────────────────

type Handler = (...args: unknown[]) => void

class FakeParticipant {
  identity: string
  name: string
  metadata: string | null = null
  private spuren = new Map<string, { isMuted: boolean; track: unknown }>()

  constructor(identity: string, name = '', metadata: string | null = null) {
    this.identity = identity
    this.name = name
    this.metadata = metadata
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

  tritt_bei(identity: string, name = '', metadata: string | null = null) {
    const teilnehmer = new FakeParticipant(identity, name, metadata)
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
  // Liefert, ob der Browser Ton zulässt — `true` ist der Normalfall.
  erlaubeWiedergabe: vi.fn().mockResolvedValue(true),
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
  holeAktivenAnruf: vi.fn().mockResolvedValue({ has_active_call: false, call: null }),
  holeAusstehendeAnrufe: vi.fn().mockResolvedValue({ has_pending_call: false, call: null, group_calls: [] }),
  verlasseAnruf: vi.fn().mockResolvedValue(undefined),
  beendeAktivenAnrufRemote: vi.fn().mockResolvedValue(undefined),
  sendeAnrufHeartbeat: vi.fn().mockResolvedValue(undefined),
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
const toastInfo = vi.fn()
vi.mock('@/stores/toastStore', () => ({
  toast: { error: (t: string) => toastFehler(t), success: vi.fn(), info: (t: string) => toastInfo(t) },
}))

const toene = {
  toneBeitritt: vi.fn(),
  toneAbgang: vi.fn(),
  toneAufgelegt: vi.fn(),
  toneUebergabe: vi.fn(),
}
vi.mock('@/components/calling/anrufToene', () => toene)

// Nach den Mocks importieren, sonst greifen sie nicht.
const { useCallStore, setzeAnrufIdentitaet } = await import('./useCallStore')
const { getDeviceId } = await import('@/lib/deviceIdentity')

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
    expect(api.holeZugang).toHaveBeenCalledWith('direkt', 'raum-1', undefined, expect.objectContaining({ mode: 'audio' }))
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

    expect(api.holeZugang).toHaveBeenCalledWith('direkt', 'raum-9', undefined, expect.objectContaining({ mode: 'audio' }))
    expect(useCallStore.getState().state).toBe('active')
  })

  it('bricht beim Auflegen im Klingelzustand die Einladung serverseitig ab', async () => {
    api.holeZugang.mockImplementation(() => new Promise(() => {}))
    void useCallStore.getState().initiateCall(PARTNER, 'audio')
    await vi.waitFor(() => expect(useCallStore.getState().state).toBe('outgoing'))

    useCallStore.getState().endCall()
    expect(api.brichAnrufAb).toHaveBeenCalledWith('raum-1')
  })

  it('bricht beim Auflegen nach Connected aber vor Beitritt des Partners die Einladung ab', async () => {
    await useCallStore.getState().initiateCall(PARTNER, 'audio')
    aktuellerRaum.feuere(RoomEvent.Connected)
    expect(useCallStore.getState().state).toBe('active')

    useCallStore.getState().endCall()
    expect(api.brichAnrufAb).toHaveBeenCalledWith('raum-1')
  })

  it('bricht beim Auflegen nach Annahme des Partners die Einladung NICHT ab', async () => {
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u2', 'bob')

    useCallStore.getState().endCall()
    expect(api.brichAnrufAb).not.toHaveBeenCalled()
    expect(api.verlasseAnruf).toHaveBeenCalledWith('raum-1', expect.anything())
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

    expect(api.holeZugang).toHaveBeenCalledWith('gruppe', 'grp_abc', 3, expect.objectContaining({ mode: 'audio' }))
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

// ── Ton, Meldungen und Moderation ───────────────────────────────────────────

describe('Beitritt und Abgang', () => {
  it('meldet einen Beitritt flüchtig und spielt einen Ton', async () => {
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u2', 'bob')

    expect(toene.toneBeitritt).toHaveBeenCalledTimes(1)
    const hinweise = useCallStore.getState().hinweise
    expect(hinweise).toHaveLength(1)
    expect(hinweise[0].art).toBe('beitritt')
    expect(hinweise[0].text).toContain('bob')
  })

  it('meldet einen Abgang, solange noch jemand da ist', async () => {
    await useCallStore.getState().joinGroupCall(
      { id: 5, name: 'Gruppe', canShare: true, canModerate: true },
      'raum-g',
    )
    aktuellerRaum.feuere(RoomEvent.Connected)
    aktuellerRaum.tritt_bei('u2', 'bob')
    aktuellerRaum.tritt_bei('u3', 'cara')
    useCallStore.setState({ hinweise: [] })

    aktuellerRaum.verlaesst('u3')
    expect(toene.toneAbgang).toHaveBeenCalledTimes(1)
    expect(useCallStore.getState().hinweise[0].art).toBe('abgang')
  })

  it('meldet im Zweiergespräch keinen Abgang, nur das Auflegen', async () => {
    // Sonst stünde „X hat verlassen" eine Zehntelsekunde im Fenster, bevor es
    // sich schließt. Zwei Meldungen für ein Ereignis.
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u2', 'bob')
    useCallStore.setState({ hinweise: [] })

    aktuellerRaum.verlaesst('u2')
    expect(toene.toneAbgang).not.toHaveBeenCalled()
    expect(toene.toneAufgelegt).toHaveBeenCalledTimes(1)
    expect(useCallStore.getState().hinweise).toEqual([])
  })

  it('spielt beim Wegdrücken eines eingehenden Anrufs keinen Auflegeton', () => {
    useCallStore.getState().receiveCall(PARTNER, 'audio', 'raum-x')
    useCallStore.getState().rejectCall()
    expect(toene.toneAufgelegt).not.toHaveBeenCalled()
  })

  it('verwirft eine Meldung wieder', async () => {
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u2', 'bob')
    const id = useCallStore.getState().hinweise[0].id

    useCallStore.getState().verwirfHinweis(id)
    expect(useCallStore.getState().hinweise).toEqual([])
  })

  it('räumt Meldungen beim Auflegen weg', async () => {
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u2', 'bob')
    useCallStore.getState().endCall()
    expect(useCallStore.getState().hinweise).toEqual([])
  })
})

describe('Serverstumm', () => {
  it('sperrt den Mikrofonknopf, wenn die Quelle entzogen wurde', async () => {
    await verbundenerAnruf()
    // Ohne Mikrofon in den erlaubten Quellen: genau das, was ein Moderator
    // über `UpdateParticipant` setzt.
    ;(aktuellerRaum.localParticipant as unknown as { permissions: unknown }).permissions = {
      canPublishSources: [1, 3, 4],
    }
    aktuellerRaum.feuere(RoomEvent.ParticipantPermissionsChanged)

    expect(useCallStore.getState().serverStumm).toBe(true)
    expect(useCallStore.getState().isMuted).toBe(true)

    vi.clearAllMocks()
    useCallStore.getState().toggleMute()
    // Der Versuch scheitert sichtbar und ändert nichts.
    expect(livekit.setzeMikrofon).not.toHaveBeenCalled()
    expect(useCallStore.getState().isMuted).toBe(true)
    expect(toastFehler).toHaveBeenCalled()
  })

  it('lässt nach dem Aufheben wieder frei, bleibt aber stumm', async () => {
    await verbundenerAnruf()
    const teilnehmer = aktuellerRaum.localParticipant as unknown as { permissions: unknown }
    teilnehmer.permissions = { canPublishSources: [1, 3, 4] }
    aktuellerRaum.feuere(RoomEvent.ParticipantPermissionsChanged)

    teilnehmer.permissions = { canPublishSources: [1, 2, 3, 4] }
    aktuellerRaum.feuere(RoomEvent.ParticipantPermissionsChanged)

    expect(useCallStore.getState().serverStumm).toBe(false)
    // Wieder zu sprechen ist eine Entscheidung, kein Automatismus.
    expect(useCallStore.getState().isMuted).toBe(true)
    useCallStore.getState().toggleMute()
    expect(useCallStore.getState().isMuted).toBe(false)
  })

  it('hält eine leere Quellenliste für „alles erlaubt"', async () => {
    // LiveKit lässt das Feld weg, wenn nichts eingeschränkt ist. Als „nichts
    // erlaubt" gelesen wäre jeder Anruf serverstumm.
    await verbundenerAnruf()
    ;(aktuellerRaum.localParticipant as unknown as { permissions: unknown }).permissions = {
      canPublishSources: [],
    }
    aktuellerRaum.feuere(RoomEvent.ParticipantPermissionsChanged)
    expect(useCallStore.getState().serverStumm).toBe(false)
  })
})

describe('Tonwiedergabe', () => {
  it('merkt sich, wenn der Browser den Ton verweigert', async () => {
    livekit.erlaubeWiedergabe.mockResolvedValueOnce(false)
    await verbundenerAnruf()
    expect(useCallStore.getState().audioBlockiert).toBe(true)

    livekit.erlaubeWiedergabe.mockResolvedValueOnce(true)
    await useCallStore.getState().erlaubeTon()
    expect(useCallStore.getState().audioBlockiert).toBe(false)
  })
})

describe('Geräte', () => {
  it('schreibt die Wahl dorthin, wo auch der Mikrofontest sie sucht', async () => {
    await verbundenerAnruf()
    useCallStore.getState().setDevices('mic-42', undefined, 'speaker-7')

    const { getAudioSettings } = await import('@/lib/audioSettings')
    expect(getAudioSettings().preferredMicId).toBe('mic-42')
    expect(getAudioSettings().preferredSpeakerId).toBe('speaker-7')
  })
})

describe('Geräteübergreifendes Anruf-Handoff (Cross-Device)', () => {
  const FREMDER_ANRUF: ActiveCallInfo = {
    raum: 'raum-fremd-1',
    art: 'direkt',
    mode: 'audio',
    device_id: 'dev-fremdes-handy',
    device_type: 'mobile',
    group_id: null,
    group_name: null,
    partner: {
      user_id: 2,
      username: 'bob',
      avatar_url: null,
    },
    started_at: '2026-09-17T12:00:00Z',
  }

  it('checkActiveCall: erkennt laufenden Anruf auf einem anderen Gerät', async () => {
    api.holeAktivenAnruf.mockResolvedValueOnce({
      has_active_call: true,
      call: FREMDER_ANRUF,
    })

    await useCallStore.getState().checkActiveCall()

    expect(useCallStore.getState().crossDeviceCall).toEqual(FREMDER_ANRUF)
  })

  it('checkActiveCall: setzt crossDeviceCall für Wiederbeitritt wenn man lokal idle ist', async () => {
    api.holeAktivenAnruf.mockResolvedValueOnce({
      has_active_call: true,
      call: { ...FREMDER_ANRUF, device_id: getDeviceId() },
    })

    await useCallStore.getState().checkActiveCall()

    expect(useCallStore.getState().crossDeviceCall).toEqual(
      expect.objectContaining({ device_id: getDeviceId() }),
    )
  })

  it('checkActiveCall: ignoriert Anruf, wenn man lokal bereits telefoniert', async () => {
    await verbundenerAnruf()
    api.holeAktivenAnruf.mockResolvedValueOnce({
      has_active_call: true,
      call: FREMDER_ANRUF,
    })

    await useCallStore.getState().checkActiveCall()

    expect(useCallStore.getState().crossDeviceCall).toBeNull()
  })

  it('transferCallToThisDevice: holt einen direkten Anruf auf das lokale Gerät', async () => {
    useCallStore.setState({ crossDeviceCall: FREMDER_ANRUF })

    await useCallStore.getState().transferCallToThisDevice()

    expect(api.holeZugang).toHaveBeenCalledWith(
      'direkt',
      'raum-fremd-1',
      undefined,
      expect.objectContaining({ mode: 'audio' }),
    )
    expect(useCallStore.getState().crossDeviceCall).toBeNull()
    expect(useCallStore.getState().raum).toBe('raum-fremd-1')
    expect(useCallStore.getState().partner).toEqual({
      userId: 2,
      username: 'bob',
      avatarUrl: null,
    })
  })

  it('transferCallToThisDevice: holt einen Gruppenanruf auf das lokale Gerät', async () => {
    const FREMDER_GRUPPENANRUF: ActiveCallInfo = {
      raum: 'grp-raum-99',
      art: 'gruppe',
      mode: 'audio',
      device_id: 'dev-fremdes-handy',
      device_type: 'mobile',
      group_id: 10,
      group_name: 'Entwickler',
      partner: null,
      started_at: '2026-09-17T12:00:00Z',
    }
    useCallStore.setState({ crossDeviceCall: FREMDER_GRUPPENANRUF })

    await useCallStore.getState().transferCallToThisDevice()

    expect(api.holeZugang).toHaveBeenCalledWith(
      'gruppe',
      'grp-raum-99',
      10,
      expect.objectContaining({ mode: 'audio' }),
    )
    expect(useCallStore.getState().crossDeviceCall).toBeNull()
    expect(useCallStore.getState().raum).toBe('grp-raum-99')
    expect(useCallStore.getState().kind).toBe('gruppe')
    expect(useCallStore.getState().group?.name).toBe('Entwickler')
  })

  it('terminateCrossDeviceCall: beendet den Anruf auf dem anderen Gerät remote', async () => {
    useCallStore.setState({ crossDeviceCall: FREMDER_ANRUF })

    await useCallStore.getState().terminateCrossDeviceCall()

    expect(api.beendeAktivenAnrufRemote).toHaveBeenCalled()
    expect(useCallStore.getState().crossDeviceCall).toBeNull()
  })

  it('handleCrossDeviceEvent: trennt den lokalen Anruf wenn er auf anderes Gerät übertragen wurde', async () => {
    await verbundenerAnruf()
    expect(useCallStore.getState().state).toBe('active')

    useCallStore.getState().handleCrossDeviceEvent({
      type: 'call_transferred',
      raum: 'raum-1',
      old_device_id: getDeviceId(),
      new_device_id: 'dev-anderes-geraet',
    })

    expect(useCallStore.getState().state).toBe('idle')
    expect(toastInfo).toHaveBeenCalledWith(
      'Der Anruf wurde auf ein anderes Gerät übertragen.',
    )
  })

  it('handleCrossDeviceEvent: ignoriert call_transferred wenn nicht dieses Gerät abgegeben hat', async () => {
    await verbundenerAnruf()
    expect(useCallStore.getState().state).toBe('active')

    useCallStore.getState().handleCrossDeviceEvent({
      type: 'call_transferred',
      raum: 'raum-1',
      old_device_id: 'dev-jemand-anderes',
      new_device_id: 'dev-drittes-geraet',
    })

    expect(useCallStore.getState().state).toBe('active')
  })

  it('handleCrossDeviceEvent: trennt den lokalen Anruf wenn auf anderem Gerät neuer Anruf gestartet wird (superseded)', async () => {
    await verbundenerAnruf()
    expect(useCallStore.getState().state).toBe('active')

    useCallStore.getState().handleCrossDeviceEvent({
      type: 'call_superseded',
      old_raum: 'raum-1',
      new_raum: 'raum-neu-42',
    })

    expect(useCallStore.getState().state).toBe('idle')
    expect(toastInfo).toHaveBeenCalledWith(
      'Du bist auf einem anderen Gerät einem anderen Anruf beigetreten.',
    )
  })

  it('handleCrossDeviceEvent: trennt lokalen Anruf bei call_ended_remotely', async () => {
    await verbundenerAnruf()
    expect(useCallStore.getState().state).toBe('active')

    useCallStore.getState().handleCrossDeviceEvent({
      type: 'call_ended_remotely',
      raum: 'raum-1',
    })

    expect(useCallStore.getState().state).toBe('idle')
    expect(toastInfo).toHaveBeenCalledWith('Der Anruf wurde beendet.')
  })

  it('handleCrossDeviceEvent: aktualisiert crossDeviceCall bei user_call_state_changed', () => {
    useCallStore.getState().handleCrossDeviceEvent({
      type: 'user_call_state_changed',
      active_call: FREMDER_ANRUF,
    })

    expect(useCallStore.getState().crossDeviceCall).toEqual(FREMDER_ANRUF)

    useCallStore.getState().handleCrossDeviceEvent({
      type: 'user_call_state_changed',
      active_call: null,
    })

    expect(useCallStore.getState().crossDeviceCall).toBeNull()
  })

  it('direkt-Anruf: trennt nicht sofort bei ParticipantDisconnected wenn Partner Gerätewechsel signalisiert hat', async () => {
    vi.useFakeTimers()
    try {
      await verbundenerAnruf()
      aktuellerRaum.tritt_bei('u2', 'bob')
      expect(useCallStore.getState().state).toBe('active')

      // Signal vom Server: Bob wechselt das Gerät (Cross-Device Handoff)
      useCallStore.getState().handleCrossDeviceEvent({
        type: 'call_partner_transferred',
        raum: 'raum-1',
        message: 'Bob wechselt das Gerät...',
      })

      // Bobs altes Gerät verlässt den Raum
      aktuellerRaum.verlaesst('u2')

      // Soll NICHT sofort idle sein, sondern im Store aktiv bleiben mit Hinweismeldung
      expect(useCallStore.getState().state).toBe('active')
      expect(
        useCallStore.getState().hinweise.some((h) => h.text.includes('Verbindung wird wiederhergestellt')),
      ).toBe(true)

      // Wenn Bob innerhalb der Gnadenfrist mit neuem Gerät beitritt
      aktuellerRaum.tritt_bei('u2', 'bob')
      expect(useCallStore.getState().state).toBe('active')

      // Nach Ablauf von 15 Sekunden bleibt der Anruf weiterhin aktiv, weil Bob wieder da ist
      vi.advanceTimersByTime(15500)
      expect(useCallStore.getState().state).toBe('active')
    } finally {
      vi.useRealTimers()
    }
  })

  it('direkt-Anruf: beendet Gespräch nach Ablauf der Gnadenfrist wenn das neue Gerät des Partners nicht beitritt', async () => {
    vi.useFakeTimers()
    try {
      await verbundenerAnruf()
      aktuellerRaum.tritt_bei('u2', 'bob')
      expect(useCallStore.getState().state).toBe('active')

      // Signal vom Server: Bob wechselt das Gerät
      useCallStore.getState().handleCrossDeviceEvent({
        type: 'call_partner_transferred',
        raum: 'raum-1',
        message: 'Bob wechselt das Gerät...',
      })

      // Bobs altes Gerät verlässt den Raum
      aktuellerRaum.verlaesst('u2')
      expect(useCallStore.getState().state).toBe('active')

      // Nach 15+ Sekunden Gnadenfrist ohne Wiederbeitritt wird der Anruf beendet
      vi.advanceTimersByTime(15500)
      expect(useCallStore.getState().state).toBe('idle')
    } finally {
      vi.useRealTimers()
    }
  })

  it('checkActiveCall: erlaubt Wiederbeitritt auf demselben Gerät wenn noch aktiv', async () => {
    const { getDeviceId } = await import('@/lib/deviceIdentity')
    const myId = getDeviceId()
    api.holeAktivenAnruf.mockResolvedValueOnce({
      has_active_call: true,
      call: {
        ...FREMDER_ANRUF,
        device_id: myId, // Selbes Gerät (z. B. nach Neuladen der Seite)
      },
    })
    api.holeAusstehendeAnrufe.mockResolvedValueOnce({
      has_pending_call: false,
      call: null,
      group_calls: [],
    })

    await useCallStore.getState().checkActiveCall()

    // crossDeviceCall soll gesetzt sein, damit der Wiederbeitritts-Banner angezeigt wird
    expect(useCallStore.getState().crossDeviceCall).toEqual(
      expect.objectContaining({ device_id: myId }),
    )
  })

  it('handleCrossDeviceEvent: zeigt Hinweis und spielt Ton bei call_partner_transferred', async () => {
    await verbundenerAnruf()
    expect(useCallStore.getState().state).toBe('active')

    useCallStore.getState().handleCrossDeviceEvent({
      type: 'call_partner_transferred',
      raum: 'raum-1',
      message: 'Bob wechselt das Gerät...',
    })

    expect(toene.toneUebergabe).toHaveBeenCalled()
    expect(
      useCallStore.getState().hinweise.some((h) => h.text === 'Bob wechselt das Gerät...'),
    ).toBe(true)
  })

  it('transferCallToThisDevice: spielt toneUebergabe bei erfolgreicher Übernahme', async () => {
    useCallStore.setState({ crossDeviceCall: FREMDER_ANRUF })

    await useCallStore.getState().transferCallToThisDevice()

    expect(toene.toneUebergabe).toHaveBeenCalled()
  })

  it('automatischer Timeout: eingehender Anruf wird nach 60 Sekunden verworfen', () => {
    vi.useFakeTimers()
    try {
      useCallStore.getState().receiveCall(PARTNER, 'audio', 'raum-timeout-in')
      expect(useCallStore.getState().state).toBe('incoming')

      vi.advanceTimersByTime(60000)
      expect(useCallStore.getState().state).toBe('idle')
      expect(toastInfo).toHaveBeenCalledWith('Anruf verpasst.')
    } finally {
      vi.useRealTimers()
    }
  })

  it('automatischer Timeout: ausgehender Anruf wird nach 60 Sekunden beendet', async () => {
    vi.useFakeTimers()
    try {
      api.holeZugang.mockImplementation(() => new Promise(() => {}))
      void useCallStore.getState().initiateCall(PARTNER, 'audio')

      await vi.waitFor(() => expect(useCallStore.getState().state).toBe('outgoing'))

      vi.advanceTimersByTime(60000)
      expect(useCallStore.getState().state).toBe('idle')
      expect(toastInfo).toHaveBeenCalledWith('Niemand hat abgenommen.')
    } finally {
      vi.useRealTimers()
    }
  })

  it('sammleTeilnehmer: liest Avatar und Benutzername aus LiveKit-Metadaten', async () => {
    await verbundenerAnruf()
    const meta = JSON.stringify({
      user_id: 2,
      username: 'Alice-Neu',
      avatar_url: '/media/avatar-alice.jpg',
    })
    aktuellerRaum.tritt_bei('u2', 'Alice', meta)

    const participants = useCallStore.getState().participants
    const remote = participants.find((p) => p.userId === 2)
    expect(remote).toBeDefined()
    expect(remote?.avatarUrl).toBe('/media/avatar-alice.jpg')
    expect(remote?.username).toBe('Alice-Neu')
  })

  it('handleCallSyncEvent: verarbeitet direct_call_invitation', () => {
    useCallStore.getState().handleCallSyncEvent({
      type: 'direct_call_invitation',
      caller_id: 42,
      caller_username: 'Charly',
      caller_avatar_url: '/avatar/charly.png',
      mode: 'video',
      signaling_token: 'raum-sync-42',
    })

    const state = useCallStore.getState()
    expect(state.state).toBe('incoming')
    expect(state.partner?.username).toBe('Charly')
    expect(state.partner?.avatarUrl).toBe('/avatar/charly.png')
    expect(state.mode).toBe('video')
    expect(state.raum).toBe('raum-sync-42')
  })

  it('handleCallSyncEvent: verarbeitet direct_call_cancelled und schließt den Dialog', () => {
    useCallStore.getState().receiveCall(PARTNER, 'audio', 'raum-cancel-1')
    expect(useCallStore.getState().state).toBe('incoming')

    useCallStore.getState().handleCallSyncEvent({
      type: 'direct_call_cancelled',
      signaling_token: 'raum-cancel-1',
    })

    expect(useCallStore.getState().state).toBe('idle')
    expect(toastInfo).toHaveBeenCalledWith('Der Anrufer hat aufgelegt.')
  })

  it('handleCallSyncEvent: legt beim Anrufer auf, wenn der Angerufene ablehnt', async () => {
    // Am laufenden System gefunden: der Anrufer sass nach der Ablehnung weiter
    // im Raum und musste von Hand auflegen. Zwei Bedingungen trafen nie zu —
    // `recipient_id` nennt in diesem Ereignis den Ablehnenden, und der Zustand
    // steht längst auf `active`, weil der Anrufer den Raum schon beim Klingeln
    // betritt.
    await verbundenerAnruf()
    expect(useCallStore.getState().state).toBe('active')

    useCallStore.getState().handleCallSyncEvent({
      type: 'direct_call_rejected',
      signaling_token: 'raum-1',
      rejected_by: 2,
    })

    expect(useCallStore.getState().state).toBe('idle')
    expect(useCallStore.getState().raum).toBeNull()
    expect(livekit.trenne).toHaveBeenCalled()
    expect(toastInfo).toHaveBeenCalledWith('Der Anruf wurde abgelehnt.')
  })

  it('handleCallSyncEvent: eine Ablehnung aus einem fremden Raum lässt den Anruf stehen', async () => {
    await verbundenerAnruf()

    useCallStore.getState().handleCallSyncEvent({
      type: 'direct_call_rejected',
      signaling_token: 'raum-woanders',
      rejected_by: 9,
    })

    expect(useCallStore.getState().state).toBe('active')
  })

  it('handleCallSyncEvent: eine späte Ablehnung beendet kein laufendes Gespräch', async () => {
    // Wer nachgeholt wurde und ablehnt, während die beiden anderen sprechen,
    // darf deren Gespräch nicht mitnehmen.
    await verbundenerAnruf()
    aktuellerRaum.tritt_bei('u2', 'bob')

    useCallStore.getState().handleCallSyncEvent({
      type: 'direct_call_rejected',
      signaling_token: 'raum-1',
      rejected_by: 3,
    })

    expect(useCallStore.getState().state).toBe('active')
  })

  it('handleCallSyncEvent: verarbeitet group_call_started und group_call_ended', () => {
    useCallStore.getState().handleCallSyncEvent({
      type: 'group_call_started',
      group_id: 10,
      group_name: 'Entwickler',
      room_token: 'grp_token_10',
    })

    expect(useCallStore.getState().activeGroupCalls).toEqual([
      expect.objectContaining({
        group_id: 10,
        group_name: 'Entwickler',
        room_token: 'grp_token_10',
      }),
    ])

    useCallStore.getState().handleCallSyncEvent({
      type: 'group_call_ended',
      group_id: 10,
      room_token: 'grp_token_10',
    })

    expect(useCallStore.getState().activeGroupCalls).toEqual([])
  })

  it('checkActiveCall: holt ausstehende Anrufe und aktive Gruppenanrufe', async () => {
    api.holeAusstehendeAnrufe.mockResolvedValueOnce({
      has_pending_call: true,
      call: {
        caller_id: 99,
        caller_username: 'Dana',
        caller_avatar_url: '/avatar/dana.png',
        mode: 'audio',
        signaling_token: 'raum-pending-99',
        expires_in: 55,
      },
      group_calls: [
        {
          group_id: 7,
          group_name: 'Support',
          avatar_url: null,
          room_token: 'grp_supp_7',
          participant_count: 2,
        },
      ],
    })

    await useCallStore.getState().checkActiveCall()

    const state = useCallStore.getState()
    expect(state.state).toBe('incoming')
    expect(state.partner?.username).toBe('Dana')
    expect(state.partner?.avatarUrl).toBe('/avatar/dana.png')
    expect(state.raum).toBe('raum-pending-99')
    expect(state.activeGroupCalls).toHaveLength(1)
    expect(state.activeGroupCalls[0].group_name).toBe('Support')
  })
})
