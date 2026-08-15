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

/** Dieselbe Rate wie im Backend (`ai_voice_session.ABTASTRATE`). */
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
}

/**
 * Nimmt auf und ruft `aufPaket` für jedes fertige Stück.
 *
 * Wirft, wenn der Mensch das Mikrofon nicht freigibt oder der Browser keines
 * hat. Der Aufrufer soll das sehen — eine stumme Sitzung, in der niemand weiß,
 * warum nichts passiert, ist die schlechtere Antwort.
 */
export async function starteAufnahme(
  aufPaket: (paket: ArrayBuffer) => void,
): Promise<Aufnahme> {
  const strom = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  })

  const kontext = new AudioContext({ sampleRate: ABTASTRATE })
  const quelle = kontext.createMediaStreamSource(strom)
  const prozessor = kontext.createScriptProcessor(PAKETGROESSE, 1, 0)

  prozessor.onaudioprocess = (ereignis) => {
    aufPaket(zuInt16(ereignis.inputBuffer.getChannelData(0)))
  }

  quelle.connect(prozessor)
  // Der Prozessor hat null Ausgangskanäle und braucht trotzdem ein Ziel,
  // sonst läuft er in manchen Browsern gar nicht erst an. Er schickt nichts
  // dorthin — es hört also niemand sich selbst.
  prozessor.connect(kontext.destination)

  let beendet = false
  return {
    beenden() {
      if (beendet) return
      beendet = true
      prozessor.onaudioprocess = null
      prozessor.disconnect()
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
