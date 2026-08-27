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
import { ausgabeGeraetId } from './audioGeraete'

/**
 * Vorlauf beim Neustart nach einer Pause. Ohne ihn beginnt das erste Stück
 * exakt „jetzt", und alles, was die Einplanung noch kostet, fehlt vorne — das
 * hört man als abgeschnittene erste Silbe.
 */
const VORLAUF_SEKUNDEN = 0.08

export class Wiedergabe {
  private kontext: AudioContext | null = null
  private naechsterStart = 0
  private quellen = new Set<{ quelle: AudioBufferSourceNode; start: number; dauer: number }>()
  /**
   * Misst, wie laut gerade gesprochen wird.
   *
   * Nicht für die Wiedergabe nötig — für die **Blase**. Sie soll sich zum
   * gesprochenen Wort bewegen und nicht zu einem Zufallsgenerator, der so tut.
   * Der Unterschied ist der ganze Punkt: eine Animation, die nur ungefähr zum
   * Ton passt, sieht sofort nach Dekoration aus.
   */
  private messer: AnalyserNode | null = null
  // `Uint8Array<ArrayBuffer>` und nicht bloss `Uint8Array`: seit TypeScript 5.7
  // ist die Sicht typisiert, und `getByteTimeDomainData` nimmt ausdrücklich
  // keinen `SharedArrayBuffer`.
  private probe: Uint8Array<ArrayBuffer> | null = null

  /** Ob gerade noch etwas eingeplant ist oder läuft. */
  get spricht(): boolean {
    return this.quellen.size > 0
  }

  /**
   * Der aktuelle Pegel zwischen 0 und 1.
   *
   * Effektivwert (RMS) und nicht der Spitzenwert: der Spitzenwert springt bei
   * jedem Zischlaut auf Anschlag und lässt die Blase zappeln. RMS folgt der
   * Lautstärke, wie ein Ohr sie hört.
   */
  pegel(): number {
    const messer = this.messer
    const probe = this.probe
    if (!messer || !probe || this.quellen.size === 0) return 0
    messer.getByteTimeDomainData(probe)
    let summe = 0
    for (let i = 0; i < probe.length; i += 1) {
      const abweichung = (probe[i] - 128) / 128
      summe += abweichung * abweichung
    }
    // Der Faktor holt den RMS gesprochener Sprache (grob 0,05 bis 0,25) in
    // einen Bereich, in dem die Blase sichtbar atmet.
    return Math.min(1, Math.sqrt(summe / probe.length) * 4)
  }

  spiele(pcm: ArrayBuffer): void {
    const byteLength = pcm.byteLength - (pcm.byteLength % 2)
    if (byteLength < 2) return
    const kontext = this.hole()
    if (kontext.state === 'suspended') {
      void kontext.resume().catch(() => undefined)
    }

    const roh = new Int16Array(pcm, 0, byteLength / 2)
    const puffer = kontext.createBuffer(1, roh.length, ABTASTRATE)
    const kanal = puffer.getChannelData(0)
    for (let i = 0; i < roh.length; i += 1) {
      // Zurück nach Float32. Der Teiler ist 0x8000 und nicht 0x7fff: so bleibt
      // die Null wirklich Null und der negative Vollausschlag exakt −1.
      kanal[i] = roh[i] / 0x8000
    }

    const quelle = kontext.createBufferSource()
    quelle.buffer = puffer
    if (this.messer) {
      quelle.connect(this.messer)
    }
    quelle.connect(kontext.destination)

    const jetzt = kontext.currentTime
    if (this.naechsterStart < jetzt) {
      this.naechsterStart = jetzt + VORLAUF_SEKUNDEN
    }
    const startZeit = this.naechsterStart
    const dauer = puffer.duration

    quelle.start(startZeit)
    this.naechsterStart += dauer

    const eintrag = { quelle, start: startZeit, dauer }
    this.quellen.add(eintrag)
    quelle.onended = () => {
      this.quellen.delete(eintrag)
    }
  }

  /**
   * Die noch nicht angefangenen Stücke verwerfen — der Mensch redet dazwischen.
   * Der laufende Satz darf gemütlich zu Ende reden.
   */
  abbrechen(): void {
    const jetzt = this.kontext?.currentTime ?? 0
    let laeuftNoch = false
    let spaetesterEnde = jetzt

    for (const eintrag of [...this.quellen]) {
      // Wenn das Stück noch nicht angefangen hat, würgen wir es ab
      if (eintrag.start > jetzt + 0.1) {
        try {
          eintrag.quelle.stop()
        } catch {}
        this.quellen.delete(eintrag)
      } else {
        // Stück läuft -> wir lassen es fertig reden
        laeuftNoch = true
        const ende = eintrag.start + eintrag.dauer
        if (ende > spaetesterEnde) spaetesterEnde = ende
      }
    }

    if (laeuftNoch) {
      this.naechsterStart = spaetesterEnde
    } else {
      this.naechsterStart = 0
    }
  }

  /** Aufräumen am Ende der Sitzung. */
  schliessen(): void {
    this.abbrechen()
    void this.kontext?.close().catch(() => undefined)
    this.kontext = null
    this.messer = null
    this.probe = null
  }

  /**
   * Bereitet den AudioContext direkt bei Nutzergeste vor (Browser-Autoplay-Richtlinien).
   */
  bereitMachen(): void {
    const kontext = this.hole()
    if (kontext.state === 'suspended') {
      void kontext.resume().catch(() => undefined)
    }
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
      try {
        this.kontext = new AudioContext({ sampleRate: ABTASTRATE })
      } catch {
        this.kontext = new AudioContext()
      }
      if (this.kontext.state === 'suspended') {
        void this.kontext.resume().catch(() => undefined)
      }
      // Das Wunschgerät der Desktop-App — im Panel ein No-Op (`null`).
      // `setSinkId` gibt es erst seit Chromium 110; wo es fehlt oder das
      // Gerät weg ist, bleibt der Systemstandard — Ton geht vor Gerätetreue.
      void ausgabeGeraetId()
        .then((sink) => {
          const kontext = this.kontext as (AudioContext & {
            setSinkId?: (id: string) => Promise<void>
          }) | null
          if (sink && kontext?.setSinkId) {
            return kontext.setSinkId(sink)
          }
        })
        .catch(() => undefined)
      // Der Messpunkt liegt **vor** dem Lautsprecher: alles, was klingt, geht
      // durch ihn. Ein Browser ohne `createAnalyser` verliert damit die
      // Bewegung der Blase, nicht den Ton — der Weg zum Ziel bleibt bestehen.
      if (typeof this.kontext.createAnalyser === 'function') {
        this.messer = this.kontext.createAnalyser()
        this.messer.fftSize = 256
        this.messer.smoothingTimeConstant = 0.6
        this.messer.connect(this.kontext.destination)
        this.probe = new Uint8Array(this.messer.fftSize)
      }
    }
    if (this.kontext.state === 'suspended') {
      void this.kontext.resume().catch(() => undefined)
    }
    return this.kontext
  }
}
