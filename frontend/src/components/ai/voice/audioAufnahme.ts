/**
 * Das Mikrofon, heruntergerechnet auf das, was die Gegenstelle versteht.
 *
 * Die Kette ist kurz und jedes Glied hat einen Grund:
 *
 *   getUserMedia → AudioContext(24 kHz) → ScriptProcessor → Float32 → Int16 → WS
 *
 * **24 kHz kommt vom `AudioContext` und nicht von uns.** Der Browser kann
 * abtastraten-konvertieren, und er tut es besser als eine handgeschriebene
 * Interpolation über einem `Float32Array` — das ist Signalverarbeitung, und die
 * gehört nicht in Anwendungscode. Wir sagen ihm die gewünschte Rate und nehmen,
 * was herauskommt.
 *
 * **Warum `ScriptProcessorNode` und nicht `AudioWorklet`.** Der Worklet ist der
 * moderne Weg und braucht eine eigene Datei, die über eine URL geladen wird —
 * im Build ein zusätzliches Asset, in der Testumgebung ein Fetch, der nicht
 * geht. Der Prozessor ist als veraltet markiert, funktioniert aber in jedem
 * Browser, den MSM unterstützt, und läuft hier für Sprachpakete von 4096
 * Samples. Wenn der Worklet eines Tages die einfachere Lösung ist, betrifft der
 * Wechsel nur diese Datei.
 *
 * Echounterdrückung, Rauschsperre und Pegelanpassung macht der Browser
 * (`echoCancellation`, `noiseSuppression`, `autoGainControl`). Ohne das erste
 * hört sich die KI selbst und antwortet auf sich — das ist kein Randfall,
 * sondern der Normalfall an einem Laptop ohne Kopfhörer. Im Panel sind alle
 * drei immer an; die Desktop-App kann sie abwählen und zusätzlich eine
 * Software-Verstärkung setzen (`registriereAudioVerarbeitung`) — alles
 * Chromium-intern, kein Ton verlässt dafür den Rechner.
 */

import { aktuelleVerarbeitung, eingabeGeraetId } from './audioGeraete'

/** Dieselbe Rate wie im Backend (`ai_voice_vad.ABTASTRATE`). */
export const ABTASTRATE = 24_000

/**
 * Wieviele Samples je Paket. 4096 bei 24 kHz sind rund 170 Millisekunden —
 * klein genug, dass das Reinreden schnell ankommt, groß genug, dass nicht
 * hundertmal je Sekunde ein WebSocket-Rahmen entsteht.
 */
const PAKETGROESSE = 4096

export interface Aufnahme {
  /** Mikrofon aus, Kanäle zu. Mehrfach aufrufbar. */
  beenden(): void
  /**
   * Wie laut gerade gesprochen wird, zwischen 0 und 1.
   *
   * Fällt hier nebenbei ab: die Pakete gehen ohnehin durch, und der
   * Effektivwert kostet eine Schleife über Zahlen, die schon im Cache liegen.
   * Ein zweiter `AnalyserNode` nur zum Anzeigen wäre ein zweiter Abgriff auf
   * dasselbe Signal.
   */
  pegel(): number
}

/**
 * Warum die Aufnahme nicht zustande kam.
 *
 * Die Unterscheidung ist kein Komfort. „Kein Zugriff auf das Mikrofon" ist ein
 * Satz, der den Menschen in die Browsereinstellungen schickt — steht er dort,
 * obwohl die Freigabe längst erteilt ist, sucht er an der falschen Stelle
 * weiter. Genau das ist passiert, als der Fehler in Wahrheit aus der
 * Audiokette kam.
 */
export type Aufnahmefehler = 'verweigert' | 'audio'

export class AufnahmeAbbruch extends Error {
  constructor(readonly grund: Aufnahmefehler, readonly ursache: unknown) {
    super(`Aufnahme nicht moeglich: ${grund}`)
    this.name = 'AufnahmeAbbruch'
  }
}

/** Namen, mit denen Browser eine verweigerte oder unmögliche Freigabe melden. */
const VERWEIGERT = new Set([
  'NotAllowedError',
  'PermissionDeniedError',
  'SecurityError',
  'NotFoundError',
  'NotReadableError',
  'OverconstrainedError',
])

/**
 * Nimmt auf und ruft `aufPaket` für jedes fertige Stück.
 *
 * Wirft `AufnahmeAbbruch` mit dem Grund. Der Aufrufer soll das sehen — eine
 * stumme Sitzung, in der niemand weiß, warum nichts passiert, ist die
 * schlechtere Antwort. Und er soll den **richtigen** Grund sehen: die beiden
 * Hälften dieser Funktion scheitern aus völlig verschiedenen Anlässen.
 */
export async function starteAufnahme(
  aufPaket: (paket: ArrayBuffer) => void,
): Promise<Aufnahme> {
  // Die Gerätewahl der Desktop-App; im Panel immer `null` (Standard).
  // `ideal` statt `exact`: ein abgezogenes Wunschgerät soll auf den Standard
  // zurückfallen, nicht mit „Mikrofon verweigert" enden.
  const geraet = await eingabeGeraetId().catch(() => null)
  const verarbeitung = aktuelleVerarbeitung()
  let strom: MediaStream
  try {
    strom = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: verarbeitung.echo,
        noiseSuppression: verarbeitung.rauschen,
        autoGainControl: verarbeitung.autogain,
        ...(geraet ? { deviceId: { ideal: geraet } } : {}),
      },
    })
  } catch (fehler) {
    const name = (fehler as { name?: string })?.name ?? ''
    // Fehlt `navigator.mediaDevices` ganz — unsicherer Kontext, also HTTP ohne
    // localhost —, kommt hier ein `TypeError` an. Auch das ist eine Frage der
    // Freigabe und keine der Audiokette.
    throw new AufnahmeAbbruch(
      VERWEIGERT.has(name) || name === 'TypeError' ? 'verweigert' : 'audio',
      fehler,
    )
  }

  try {
    return baueKette(strom, aufPaket)
  } catch (fehler) {
    // Die Freigabe war erteilt; die Kette dahinter ist gescheitert. Das
    // Mikrofon muss trotzdem zugehen, sonst bleibt der rote Punkt im Tab
    // stehen für eine Aufnahme, die es nicht gibt.
    strom.getTracks().forEach((spur) => spur.stop())
    throw new AufnahmeAbbruch('audio', fehler)
  }
}

function resampleFloat32(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate || fromRate <= 0 || toRate <= 0) return input
  const ratio = fromRate / toRate
  const outLength = Math.round(input.length / ratio)
  const result = new Float32Array(outLength)
  for (let i = 0; i < outLength; i++) {
    const srcIndex = Math.min(input.length - 1, Math.round(i * ratio))
    result[i] = input[srcIndex]
  }
  return result
}

function baueKette(
  strom: MediaStream,
  aufPaket: (paket: ArrayBuffer) => void,
): Aufnahme {
  let kontext: AudioContext
  try {
    kontext = new AudioContext({ sampleRate: ABTASTRATE })
  } catch {
    kontext = new AudioContext()
  }

  if (kontext.state === 'suspended') {
    void kontext.resume().catch(() => {})
  }

  const quelle = kontext.createMediaStreamSource(strom)
  const prozessor = kontext.createScriptProcessor(PAKETGROESSE, 1, 1)

  // Die Software-Verstärkung sitzt **vor** dem Prozessor: was der Mensch am
  // Regler einstellt, ist genau das, was die Gegenstelle hört — und was der
  // Pegel unten anzeigt. Bei 1,0 ist der Knoten ein Durchlauf.
  const eingang = kontext.createGain()
  eingang.gain.value = aktuelleVerarbeitung().verstaerkung

  // Geglättet, nicht roh. Ein ungeglätteter Pegel springt bei jedem Zischlaut
  // auf Anschlag; die Blase zappelte dann, statt zu atmen.
  let geglaettet = 0
  prozessor.onaudioprocess = (ereignis) => {
    if (kontext.state === 'suspended') {
      void kontext.resume().catch(() => {})
    }
    const werte = ereignis.inputBuffer.getChannelData(0)
    geglaettet = geglaettet * 0.7 + effektivwert(werte) * 0.3
    const resampled = kontext.sampleRate === ABTASTRATE
      ? werte
      : resampleFloat32(werte, kontext.sampleRate, ABTASTRATE)
    aufPaket(zuInt16(resampled))
  }

  // Ein `ScriptProcessorNode` läuft nur an, wenn sein Ausgang irgendwo endet —
  // auch dann, wenn niemand ihn hören soll. Der Weg dorthin führt deshalb über
  // eine Verstärkung von null.
  const stumm = kontext.createGain()
  stumm.gain.value = 0
  quelle.connect(eingang)
  eingang.connect(prozessor)
  prozessor.connect(stumm)
  stumm.connect(kontext.destination)

  let beendet = false
  return {
    pegel: () => (beendet ? 0 : geglaettet),
    beenden() {
      if (beendet) return
      beendet = true
      prozessor.onaudioprocess = null
      prozessor.disconnect()
      stumm.disconnect()
      eingang.disconnect()
      quelle.disconnect()
      strom.getTracks().forEach((spur) => spur.stop())
      void kontext.close().catch(() => undefined)
    },
  }
}

/**
 * Der Effektivwert eines Blocks, auf 0 bis 1 gebracht.
 */
function effektivwert(eingabe: Float32Array): number {
  let summe = 0
  for (let i = 0; i < eingabe.length; i += 1) summe += eingabe[i] * eingabe[i]
  return Math.min(1, Math.sqrt(summe / Math.max(1, eingabe.length)) * 4)
}

/**
 * Float32 (−1 bis 1) zu Int16 Little Endian — das Format der Gegenstelle.
 */
function zuInt16(eingabe: Float32Array): ArrayBuffer {
  const ausgabe = new Int16Array(eingabe.length)
  for (let i = 0; i < eingabe.length; i += 1) {
    const wert = Math.max(-1, Math.min(1, eingabe[i]))
    ausgabe[i] = wert < 0 ? wert * 0x8000 : wert * 0x7fff
  }
  return ausgabe.buffer
}
