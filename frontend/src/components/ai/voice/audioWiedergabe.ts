/**
 * Die Stimme der KI — Tonstücke, die nahtlos aneinander hängen müssen.
 *
 * Das Problem ist kleiner als es klingt und hat genau eine Falle: die Stücke
 * kommen über das Netz, also unregelmäßig, und sie müssen **regelmäßig**
 * abgespielt werden. Wer jedes Stück bei „jetzt" startet, bekommt bei jedem
 * verspäteten Paket eine hörbare Lücke und bei jedem frühen eine Überlappung.
 *
 * Die Lösung ist eine mitlaufende Uhr: `naechsterStart` merkt sich, wann das
 * zuletzt eingeplante Stück endet, und das nächste beginnt genau dort. Nur wenn
 * die Uhr hinter die Gegenwart gefallen ist — weil eine Pause war —, wird sie
 * auf jetzt plus einen kleinen Vorlauf gesetzt.
 *
 * **Abbrechen muss sofort wirken.** Wenn der Mensch dazwischenredet, ist alles
 * schon Eingeplante falsch: es ist die Antwort auf die vorige Frage. Deshalb
 * hält `quellen` jedes laufende Stück fest, und `abbrechen()` stoppt sie alle.
 * Ohne das redet die KI noch zehn Sekunden weiter, obwohl sie längst
 * unterbrochen wurde.
 */

import { ABTASTRATE } from './audioAufnahme'

/**
 * Vorlauf beim Neustart nach einer Pause. Ohne ihn beginnt das erste Stück
 * exakt „jetzt", und alles, was die Einplanung noch kostet, fehlt vorne — das
 * hört man als abgeschnittene erste Silbe.
 */
const VORLAUF_SEKUNDEN = 0.08

export class Wiedergabe {
  private kontext: AudioContext | null = null
  private naechsterStart = 0
  private quellen = new Set<AudioBufferSourceNode>()

  /** Ob gerade noch etwas eingeplant ist oder läuft. */
  get spricht(): boolean {
    return this.quellen.size > 0
  }

  /**
   * Ein Tonstück einreihen. `pcm` ist Int16 Little Endian bei 24 kHz — genau
   * das, was das Backend binär durchreicht.
   */
  spiele(pcm: ArrayBuffer): void {
    if (pcm.byteLength < 2) return
    const kontext = this.hole()

    const roh = new Int16Array(pcm)
    const puffer = kontext.createBuffer(1, roh.length, ABTASTRATE)
    const kanal = puffer.getChannelData(0)
    for (let i = 0; i < roh.length; i += 1) {
      // Zurück nach Float32. Der Teiler ist 0x8000 und nicht 0x7fff: so bleibt
      // die Null wirklich Null und der negative Vollausschlag exakt −1.
      kanal[i] = roh[i] / 0x8000
    }

    const quelle = kontext.createBufferSource()
    quelle.buffer = puffer
    quelle.connect(kontext.destination)

    const jetzt = kontext.currentTime
    if (this.naechsterStart < jetzt) {
      this.naechsterStart = jetzt + VORLAUF_SEKUNDEN
    }
    quelle.start(this.naechsterStart)
    this.naechsterStart += puffer.duration

    this.quellen.add(quelle)
    quelle.onended = () => {
      this.quellen.delete(quelle)
    }
  }

  /**
   * Alles Laufende und Eingeplante verwerfen — der Mensch redet dazwischen.
   *
   * `stop()` löst `onended` aus, deshalb wird über eine Kopie gelaufen: sonst
   * würde das Set währenddessen verändert, über das gerade iteriert wird.
   */
  abbrechen(): void {
    for (const quelle of [...this.quellen]) {
      try {
        quelle.stop()
      } catch {
        // Ein Stück, das noch nie lief oder schon vorbei ist, wirft. Beides
        // ist genau das, was wir wollten.
      }
    }
    this.quellen.clear()
    this.naechsterStart = 0
  }

  /** Aufräumen am Ende der Sitzung. */
  schliessen(): void {
    this.abbrechen()
    void this.kontext?.close().catch(() => undefined)
    this.kontext = null
  }

  /**
   * Der Kontext entsteht erst beim ersten Ton.
   *
   * Ein `AudioContext`, den keine Nutzergeste gestartet hat, bleibt in den
   * meisten Browsern `suspended`. Beim ersten Stück hat der Mensch längst auf
   * den Sprachknopf gedrückt — der Kontext startet also im richtigen Moment,
   * und `resume()` fängt den Rest.
   */
  private hole(): AudioContext {
    if (this.kontext === null) {
      this.kontext = new AudioContext({ sampleRate: ABTASTRATE })
    }
    if (this.kontext.state === 'suspended') {
      void this.kontext.resume().catch(() => undefined)
    }
    return this.kontext
  }
}
