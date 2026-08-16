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
 * sondern der Normalfall an einem Laptop ohne Kopfhörer.
 */

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
  let strom: MediaStream
  try {
    strom = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
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

function baueKette(
  strom: MediaStream,
  aufPaket: (paket: ArrayBuffer) => void,
): Aufnahme {
  const kontext = new AudioContext({ sampleRate: ABTASTRATE })
  const quelle = kontext.createMediaStreamSource(strom)
  const prozessor = kontext.createScriptProcessor(PAKETGROESSE, 1, 1)

  // Geglättet, nicht roh. Ein ungeglätteter Pegel springt bei jedem Zischlaut
  // auf Anschlag; die Blase zappelte dann, statt zu atmen.
  let geglaettet = 0
  prozessor.onaudioprocess = (ereignis) => {
    const werte = ereignis.inputBuffer.getChannelData(0)
    geglaettet = geglaettet * 0.7 + effektivwert(werte) * 0.3
    aufPaket(zuInt16(werte))
  }

  // Ein `ScriptProcessorNode` läuft nur an, wenn sein Ausgang irgendwo endet —
  // auch dann, wenn niemand ihn hören soll. Der Weg dorthin führt deshalb über
  // eine Verstärkung von null.
  //
  // Hier stand `createScriptProcessor(…, 1, 0)` mit einem Kommentar, der
  // erklärte, warum das sicher sei. Es war das Gegenteil: Chrome wirft beim
  // Verbinden `InvalidAccessError: cannot connect a ScriptProcessorNode with 0
  // output channels to any destination node`. Das passierte **nach** der
  // Mikrofonfreigabe, und der Aufrufer meldete daraufhin „kein Zugriff auf das
  // Mikrofon" — eine Meldung über eine Erlaubnis, die längst erteilt war.
  //
  // Ein Ausgangskanal und `gain = 0`. Die Verstärkung ist nicht Zierrat: ohne
  // sie hinge das Mikrofon am Lautsprecher, und das ist keine leise Panne,
  // sondern eine Rückkopplung.
  const stumm = kontext.createGain()
  stumm.gain.value = 0
  quelle.connect(prozessor)
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
      quelle.disconnect()
      // Ohne dieses `stop()` bleibt die Aufnahmeanzeige des Browsers stehen,
      // auch wenn niemand mehr zuhört. Das ist kein Schönheitsfehler: der
      // rote Punkt im Tab ist das Einzige, woran ein Mensch erkennt, ob sein
      // Mikrofon offen ist.
      strom.getTracks().forEach((spur) => spur.stop())
      void kontext.close().catch(() => undefined)
    },
  }
}

/**
 * Der Effektivwert eines Blocks, auf 0 bis 1 gebracht.
 *
 * RMS und nicht der Spitzenwert: der Spitzenwert einer normal gesprochenen
 * Silbe liegt nahe eins und sagt damit nichts. Der Faktor holt gesprochene
 * Sprache (grob 0,05 bis 0,25) in einen Bereich, in dem man Bewegung sieht.
 */
function effektivwert(eingabe: Float32Array): number {
  let summe = 0
  for (let i = 0; i < eingabe.length; i += 1) summe += eingabe[i] * eingabe[i]
  return Math.min(1, Math.sqrt(summe / Math.max(1, eingabe.length)) * 4)
}

/**
 * Float32 (−1 bis 1) zu Int16 Little Endian — das Format der Gegenstelle.
 *
 * Geklemmt wird ausdrücklich: ein Wert über 1 entsteht bei übersteuerter
 * Eingabe, und ohne Klemmung liefe er über und würde aus einem lauten Wort ein
 * Knacken. Die beiden Faktoren sind verschieden, weil Int16 unten eine Stufe
 * mehr hat als oben (−32768 bis 32767).
 */
function zuInt16(eingabe: Float32Array): ArrayBuffer {
  const ausgabe = new Int16Array(eingabe.length)
  for (let i = 0; i < eingabe.length; i += 1) {
    const wert = Math.max(-1, Math.min(1, eingabe[i]))
    ausgabe[i] = wert < 0 ? wert * 0x8000 : wert * 0x7fff
  }
  return ausgabe.buffer
}
