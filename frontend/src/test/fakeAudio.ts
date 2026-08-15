/**
 * Web-Audio-Attrappen für Frontend-Tests.
 *
 * jsdom hat weder `AudioContext` noch `navigator.mediaDevices`. Die Attrappen
 * hier bilden genau die Oberfläche nach, die `audioAufnahme.ts` und
 * `audioWiedergabe.ts` benutzen — nicht mehr. Eine vollständige Web-Audio-
 * Nachbildung wäre ein zweites Projekt und würde nichts beweisen, was die
 * echten Browser nicht besser beweisen.
 *
 * Was sie **wohl** beweisen kann und soll: dass Pakete als Int16 rausgehen,
 * dass abgebrochene Wiedergabe wirklich stoppt, dass die Uhr weiterläuft und
 * dass `beenden()` die Mikrofonspur schließt. Das sind Zusagen, die dieser Code
 * gibt, und sie sind hier prüfbar.
 *
 * Nach dem Muster von [`fakeWebSocket.ts`](./fakeWebSocket.ts).
 */

export class FakeAudioBuffer {
  readonly duration: number
  private readonly daten: Float32Array

  constructor(
    readonly numberOfChannels: number,
    readonly length: number,
    readonly sampleRate: number,
  ) {
    this.daten = new Float32Array(length)
    this.duration = length / sampleRate
  }

  getChannelData(): Float32Array {
    return this.daten
  }
}

export class FakeBufferSource {
  buffer: FakeAudioBuffer | null = null
  onended: (() => void) | null = null
  /** Wann `start()` gerufen wurde — das ist die Zusage des Jitter-Puffers. */
  startZeit: number | null = null
  gestoppt = false

  connect(): void {}
  disconnect(): void {}

  start(zeit: number): void {
    this.startZeit = zeit
  }

  stop(): void {
    this.gestoppt = true
    // Der echte Knoten meldet `onended` nach dem Stoppen. Ohne das bliebe das
    // Quellen-Set im Test voll und `spricht` dauerhaft wahr.
    this.onended?.()
  }
}

export class FakeScriptProcessor {
  onaudioprocess: ((ereignis: { inputBuffer: { getChannelData: () => Float32Array } }) => void) | null = null
  verbunden = false

  constructor(readonly groesse: number, readonly ausgangskanaele = 1) {}

  connect(): void {
    // Die Regel des Browsers, nachgebildet. Sie fehlte hier, und deshalb ging
    // ein echter Fehler durch die ganze Testsuite: die Aufnahme verband einen
    // Prozessor mit null Ausgangskanälen, Chrome warf `InvalidAccessError`, und
    // der Sprachmodus meldete „kein Zugriff auf das Mikrofon" — obwohl die
    // Freigabe erteilt war. Eine Attrappe, die nachsichtiger ist als die
    // Wirklichkeit, prüft nichts; sie bestätigt nur.
    if (this.ausgangskanaele === 0) {
      const fehler = new Error(
        'cannot connect a ScriptProcessorNode with 0 output channels to any destination node.',
      )
      fehler.name = 'InvalidAccessError'
      throw fehler
    }
    this.verbunden = true
  }

  disconnect(): void {
    this.verbunden = false
  }

  /** Test-Helfer: ein Eingangspaket durch die Kette schicken. */
  sende(werte: Float32Array): void {
    this.onaudioprocess?.({ inputBuffer: { getChannelData: () => werte } })
  }
}

export class FakeAudioContext {
  static instances: FakeAudioContext[] = []

  readonly sampleRate: number
  currentTime = 0
  state: 'running' | 'suspended' = 'running'
  readonly destination = {}
  readonly quellen: FakeBufferSource[] = []
  readonly prozessoren: FakeScriptProcessor[] = []
  readonly verstaerker: { gain: { value: number } }[] = []
  geschlossen = false

  constructor(optionen?: { sampleRate?: number }) {
    this.sampleRate = optionen?.sampleRate ?? 48_000
    FakeAudioContext.instances.push(this)
  }

  createBuffer(kanaele: number, laenge: number, rate: number): FakeAudioBuffer {
    return new FakeAudioBuffer(kanaele, laenge, rate)
  }

  createBufferSource(): FakeBufferSource {
    const quelle = new FakeBufferSource()
    this.quellen.push(quelle)
    return quelle
  }

  createMediaStreamSource(): { connect: () => void; disconnect: () => void } {
    return { connect: () => undefined, disconnect: () => undefined }
  }

  createScriptProcessor(
    groesse: number,
    _eingang = 1,
    ausgang = 1,
  ): FakeScriptProcessor {
    const prozessor = new FakeScriptProcessor(groesse, ausgang)
    this.prozessoren.push(prozessor)
    return prozessor
  }

  createGain(): { gain: { value: number }; connect: () => void; disconnect: () => void } {
    const knoten = {
      gain: { value: 1 },
      connect: () => undefined,
      disconnect: () => undefined,
    }
    this.verstaerker.push(knoten)
    return knoten
  }

  resume(): Promise<void> {
    this.state = 'running'
    return Promise.resolve()
  }

  close(): Promise<void> {
    this.geschlossen = true
    return Promise.resolve()
  }
}

export class FakeSpur {
  gestoppt = false
  stop(): void {
    this.gestoppt = true
  }
}

export class FakeMediaStream {
  readonly spuren = [new FakeSpur()]
  getTracks(): FakeSpur[] {
    return this.spuren
  }
}

interface Aufbau {
  restore: () => void
  kontexte: FakeAudioContext[]
  /** Die zuletzt ausgehändigte Mediaspur — für die Prüfung, ob sie zugeht. */
  letzterStrom: () => FakeMediaStream | null
  /** Was `getUserMedia` bekommen hat. */
  letzteAnfrage: () => unknown
}

/**
 * Setzt `AudioContext` und `navigator.mediaDevices` global und gibt sie danach
 * wieder frei. `verweigern: true` lässt `getUserMedia` scheitern — der Fall,
 * in dem der Mensch das Mikrofon nicht freigibt.
 */
export function installFakeAudio(optionen?: { verweigern?: boolean }): Aufbau {
  const vorher = (globalThis as { AudioContext?: unknown }).AudioContext
  const vorherigeGeraete = (navigator as { mediaDevices?: unknown }).mediaDevices

  FakeAudioContext.instances = []
  let strom: FakeMediaStream | null = null
  let anfrage: unknown = null

  ;(globalThis as { AudioContext?: unknown }).AudioContext = FakeAudioContext
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: (wunsch: unknown) => {
        anfrage = wunsch
        if (optionen?.verweigern) {
          // Browser melden das über `name`, nicht über den Text. Stand hier
          // als schlichter `Error` mit „NotAllowedError" als Botschaft — und
          // damit prüfte der Test eine Unterscheidung, die es so nie gibt.
          const fehler = new Error('Permission denied')
          fehler.name = 'NotAllowedError'
          return Promise.reject(fehler)
        }
        strom = new FakeMediaStream()
        return Promise.resolve(strom)
      },
    },
  })

  return {
    kontexte: FakeAudioContext.instances,
    letzterStrom: () => strom,
    letzteAnfrage: () => anfrage,
    restore: () => {
      if (vorher) (globalThis as { AudioContext?: unknown }).AudioContext = vorher
      else delete (globalThis as { AudioContext?: unknown }).AudioContext
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: vorherigeGeraete,
      })
    },
  }
}
