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
 * Zwei Dinge fehlten hier lange, und beide Lücken waren nicht harmlos: der
 * Messpunkt (`createAnalyser`) und die Autoplay-Sperre der Browser. Ohne den
 * ersten sprang die Wiedergabe im Test immer über ihren Messzweig, `pegel()`
 * lieferte durchweg 0, und die Bewegung der Sprachblase hatte keinen einzigen
 * Test. Ohne die zweite startete jeder Kontext als `running` und `resume()`
 * gelang immer — ein Lautsprecher, der beim Menschen stumm bleibt, weil ihn
 * niemand entsperrt hat, fiele hier niemandem auf. Eine Attrappe, die
 * nachsichtiger ist als die Wirklichkeit, prüft nichts; sie bestätigt nur.
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
  /**
   * Woran das Stück hängt. Die Reihenfolge ist egal, die Menge nicht: der
   * Messpunkt darf dazukommen, das Ziel darf dabei nicht wegfallen — sonst
   * misst die Blase einen Ton, den niemand hört.
   */
  readonly ziele: unknown[] = []

  connect(ziel?: unknown): void {
    this.ziele.push(ziel)
  }

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

/**
 * Der Messpunkt, an dem die Sprachblase abliest, wie laut gerade gesprochen
 * wird.
 *
 * `welle` ist die Auslenkung um die Ruhelage 128, in denselben Byte-Schritten,
 * die ein echter `AnalyserNode` liefert. Ausgegeben wird eine Rechteckwelle:
 * damit ist der Effektivwert **genau** die Auslenkung, und der Test muss die
 * Rechnung der Wiedergabe nicht nachbauen, um sie zu prüfen.
 */
export class FakeMesser {
  fftSize = 2048
  smoothingTimeConstant = 0
  verbunden = false
  welle = 0

  connect(): void {
    this.verbunden = true
  }

  disconnect(): void {
    this.verbunden = false
  }

  getByteTimeDomainData(ziel: Uint8Array): void {
    for (let i = 0; i < ziel.length; i += 1) {
      ziel[i] = 128 + (i % 2 === 0 ? this.welle : -this.welle)
    }
  }
}

export class FakeAudioContext {
  static instances: FakeAudioContext[] = []
  /** Womit neue Kontexte starten — siehe `gesperrt` in `installFakeAudio`. */
  static startzustand: 'running' | 'suspended' = 'running'
  /** Ob `resume()` scheitert: der Browser hebt die Sperre nicht auf. */
  static entsperrenScheitert = false
  /** Ob dieser Browser `createAnalyser` überhaupt kennt. */
  static mitMesser = true

  readonly sampleRate: number
  currentTime = 0
  state: 'running' | 'suspended'
  readonly destination = {}
  readonly quellen: FakeBufferSource[] = []
  readonly prozessoren: FakeScriptProcessor[] = []
  readonly verstaerker: { gain: { value: number } }[] = []
  readonly messer: FakeMesser[] = []
  /** Wie oft jemand versucht hat, den Ton zu entsperren. */
  resumeAufrufe = 0
  geschlossen = false
  /**
   * Eine Eigenschaft und keine Methode, weil die Wiedergabe genau das prüft:
   * `typeof kontext.createAnalyser === 'function'`. Ein Browser ohne Messpunkt
   * hat sie schlicht nicht — eine Methode, die `null` zurückgibt, wäre eine
   * andere Wirklichkeit als die, gegen die der Produktivcode sich absichert.
   */
  createAnalyser: (() => FakeMesser) | undefined

  constructor(optionen?: { sampleRate?: number }) {
    this.sampleRate = optionen?.sampleRate ?? 48_000
    this.state = FakeAudioContext.startzustand
    if (FakeAudioContext.mitMesser) {
      this.createAnalyser = () => {
        const messer = new FakeMesser()
        this.messer.push(messer)
        return messer
      }
    }
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

  createMediaStreamDestination(): { stream: FakeMediaStream } {
    return { stream: new FakeMediaStream() }
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
    this.resumeAufrufe += 1
    if (FakeAudioContext.entsperrenScheitert) {
      // So melden Browser eine Sperre, die sie nicht aufheben: als abgelehntes
      // Versprechen mit `NotAllowedError`, nicht als geworfener Fehler.
      const fehler = new Error('The user did not interact with the document first')
      fehler.name = 'NotAllowedError'
      return Promise.reject(fehler)
    }
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
 * wieder frei.
 *
 * - `verweigern`: `getUserMedia` scheitert — der Mensch gibt das Mikrofon nicht
 *   frei.
 * - `gesperrt`: neue Kontexte starten `suspended` — die Autoplay-Sperre der
 *   Browser, die es ohne Nutzergeste immer gibt.
 * - `entsperrenScheitert`: `resume()` wird abgelehnt und die Sperre bleibt.
 * - `ohneMesser`: der Browser kennt `createAnalyser` nicht.
 */
export function installFakeAudio(optionen?: {
  verweigern?: boolean
  gesperrt?: boolean
  entsperrenScheitert?: boolean
  ohneMesser?: boolean
}): Aufbau {
  const vorher = (globalThis as { AudioContext?: unknown }).AudioContext
  const vorherigeGeraete = (navigator as { mediaDevices?: unknown }).mediaDevices

  FakeAudioContext.instances = []
  // Wie `instances` bei jedem Aufbau zurückgesetzt: sonst trägt ein Test seine
  // Sperre in den nächsten.
  FakeAudioContext.startzustand = optionen?.gesperrt ? 'suspended' : 'running'
  FakeAudioContext.entsperrenScheitert = optionen?.entsperrenScheitert === true
  FakeAudioContext.mitMesser = optionen?.ohneMesser !== true
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
