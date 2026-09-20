/**
 * Die Zusagen, die diese Datei über verschlüsselte Anrufe macht.
 *
 * Ihr Kopf sagt: „Kann der Browser das nicht, scheitert der Anruf — er wird
 * nicht still unverschlüsselt geführt", und über `verbinde` steht, der
 * Schlüssel werde gesetzt, *bevor* verbunden wird. Beides stand bis 09/2026
 * ungeprüft da.
 *
 * Geprüft wurde stattdessen `test/e2e/insertableStreamsE2ee.test.ts`: 484
 * Zeilen, die sich ein eigenes RTP-Rahmenverfahren mit 21-Byte-Trailer
 * definierten und dieses dann prüften. MSM schreibt so etwas nicht, Anrufe
 * laufen über LiveKit. Die Datei belegte unter Überschriften wie „Passive
 * Eavesdropping" und „Man-in-the-Middle" ein Verfahren, das es im Panel nie
 * gab, und ist deshalb weg.
 *
 * Was MSM selbst tut, ist klein: das Tor, der Schlüssel, die Reihenfolge und
 * der Codec. Genau das steht hier.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const zustand = vi.hoisted(() => ({
  unterstuetzt: true,
  wirftBeimPruefen: false,
  ablauf: [] as string[],
  letzterRaum: null as { optionen: Record<string, unknown> } | null,
  kamera: [] as Array<{
    an: boolean
    aufnahme?: { resolution?: { width: number; height: number; frameRate: number } }
    senden?: { videoCodec?: string; videoEncoding?: { maxBitrate: number; maxFramerate: number } }
  }>,
}))

vi.mock('livekit-client/e2ee-worker?worker', () => ({
  default: class {
    terminate() {}
  },
}))

vi.mock('livekit-client', () => {
  class ExternalE2EEKeyProvider {
    gesetzt: ArrayBuffer[] = []
    async setKey(schluessel: ArrayBuffer) {
      zustand.ablauf.push('schluessel')
      this.gesetzt.push(schluessel)
    }
  }
  class Room {
    optionen: Record<string, unknown>
    constructor(optionen: Record<string, unknown>) {
      this.optionen = optionen
      zustand.letzterRaum = this
    }
    async setE2EEEnabled(an: boolean) {
      zustand.ablauf.push(`e2ee:${an}`)
    }
    async connect() {
      zustand.ablauf.push('verbunden')
    }
    async disconnect() {
      zustand.ablauf.push('getrennt')
    }
  }
  return {
    Room,
    ExternalE2EEKeyProvider,
    isE2EESupported: () => {
      if (zustand.wirftBeimPruefen) throw new Error('kein Insertable Streams')
      return zustand.unterstuetzt
    },
    ConnectionState: { Connected: 'connected', Disconnected: 'disconnected' },
    RoomEvent: {},
    Track: { Source: { Camera: 'camera', Microphone: 'microphone', ScreenShare: 'screen_share' } },
  }
})

import { E2eeNichtUnterstuetzt, e2eeMoeglich, setzeRaumSchluessel, verbinde } from './livekitRaum'
import { saveVideoSettings } from '@/lib/videoSettings'

const SCHLUESSEL = new Uint8Array(32).fill(7).buffer

beforeEach(() => {
  localStorage.clear()
  zustand.unterstuetzt = true
  zustand.wirftBeimPruefen = false
  zustand.ablauf = []
  zustand.letzterRaum = null
})

describe('Das Tor', () => {
  it('lässt keinen Anruf zu, den der Browser nicht verschlüsseln kann', async () => {
    // Die Zusage aus dem Kopf der Datei. Ein Anruf, der hier durchrutscht,
    // liefe im Klartext über den SFU.
    zustand.unterstuetzt = false

    await expect(verbinde('wss://sfu', 'token', SCHLUESSEL)).rejects.toBeInstanceOf(
      E2eeNichtUnterstuetzt
    )
    expect(zustand.ablauf).not.toContain('verbunden')
    expect(zustand.letzterRaum).toBeNull()
  })

  it('wertet einen Fehler bei der Prüfung als „kann nicht"', async () => {
    // Ein SDK, das beim Prüfen wirft, darf nicht als Zustimmung gelten.
    zustand.wirftBeimPruefen = true

    expect(e2eeMoeglich()).toBe(false)
    await expect(verbinde('wss://sfu', 'token', SCHLUESSEL)).rejects.toBeInstanceOf(
      E2eeNichtUnterstuetzt
    )
  })
})

describe('Die Reihenfolge', () => {
  it('setzt Schlüssel und Verschlüsselung, bevor verbunden wird', async () => {
    // Stünde `connect` vorn, gäbe es ein Fenster, in dem Frames unverschlüsselt
    // hinausgehen. Der Kommentar über `verbinde` sagt das zu, bisher ohne Beleg.
    await verbinde('wss://sfu', 'token', SCHLUESSEL)

    expect(zustand.ablauf).toEqual(['schluessel', 'e2ee:true', 'verbunden'])
  })

  it('gibt den Raumschlüssel weiter, den es bekommen hat', async () => {
    const verbindung = await verbinde('wss://sfu', 'token', SCHLUESSEL)
    const provider = verbindung.keyProvider as unknown as { gesetzt: ArrayBuffer[] }

    expect(provider.gesetzt[0]).toBe(SCHLUESSEL)
  })

  it('reicht einen Schlüsselwechsel an den Anbieter durch', async () => {
    // Ein Nachzügler bringt seinen eigenen Schlüssel mit.
    const verbindung = await verbinde('wss://sfu', 'token', SCHLUESSEL)
    const neuer = new Uint8Array(32).fill(9).buffer

    await setzeRaumSchluessel(verbindung, neuer)

    const provider = verbindung.keyProvider as unknown as { gesetzt: ArrayBuffer[] }
    expect(provider.gesetzt).toEqual([SCHLUESSEL, neuer])
  })
})

describe('Der Raum', () => {
  it('bleibt bei VP8, weil die Verschlüsselung daran hängt', async () => {
    // Kein Stilthema: LiveKit-E2EE ist nur für VP8 in allen unterstützten
    // Browsern geprüft. Ein hübscherer Codec bliebe bei einem Gegenüber
    // schwarz, und das fiele erst im Anruf auf.
    await verbinde('wss://sfu', 'token', SCHLUESSEL)
    const veroeffentlichen = zustand.letzterRaum?.optionen.publishDefaults as {
      videoCodec: string
    }

    expect(veroeffentlichen.videoCodec).toBe('vp8')
  })

  it('bekommt Verschlüsselung und Worker mit auf den Weg', async () => {
    await verbinde('wss://sfu', 'token', SCHLUESSEL)
    const e2ee = zustand.letzterRaum?.optionen.e2ee as { keyProvider: unknown; worker: unknown }

    expect(e2ee.keyProvider).toBeDefined()
    expect(e2ee.worker).toBeDefined()
  })

  it('nimmt Auflösung und Bildrate aus dem Profil', async () => {
    // Vorher stand hier `VideoPresets.h720`, also fest 1280×720 bei 30 Bildern
    // und 1,7 Mbit/s — unabhängig davon, was Gerät und Nutzer wollten.
    saveVideoSettings({ aufloesung: '2160p', bildrate: 60 })
    await verbinde('wss://sfu', 'token', SCHLUESSEL)

    const aufnahme = zustand.letzterRaum?.optionen.videoCaptureDefaults as {
      resolution: { width: number; height: number; frameRate: number }
    }
    const veroeffentlichen = zustand.letzterRaum?.optionen.publishDefaults as {
      videoEncoding: { maxBitrate: number; maxFramerate: number }
    }

    expect(aufnahme.resolution).toEqual({ width: 3840, height: 2160, frameRate: 60 })
    expect(veroeffentlichen.videoEncoding.maxFramerate).toBe(60)
    expect(veroeffentlichen.videoEncoding.maxBitrate).toBeGreaterThan(8_000_000)
  })

  it('lässt das Bild kleiner werden statt zu ruckeln', async () => {
    // Bei einem Gesicht ist eine gehaltene Bildrate mehr wert als Schärfe.
    // Die Bildschirmfreigabe entscheidet umgekehrt.
    await verbinde('wss://sfu', 'token', SCHLUESSEL)
    const veroeffentlichen = zustand.letzterRaum?.optionen.publishDefaults as {
      degradationPreference: string
    }

    expect(veroeffentlichen.degradationPreference).toBe('balanced')
  })
})
